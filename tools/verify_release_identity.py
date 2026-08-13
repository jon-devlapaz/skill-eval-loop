#!/usr/bin/env python3
"""Verify that Git, Tink receipts, and installed skill payloads identify one commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


RECEIPT_NAME = ".tink-source.json"
REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
FULL_OBJECT_ID = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


class IdentityFailure(Exception):
    """A named release-identity boundary failed closed."""

    def __init__(self, boundary: str, detail: str) -> None:
        super().__init__(detail)
        self.boundary = boundary
        self.detail = detail


@dataclass(frozen=True)
class TreeEntry:
    kind: str
    mode: int = 0
    content: bytes = b""


@dataclass(frozen=True)
class SkillIdentity:
    name: str
    receipt_path: str
    payload_sha256: str


def _git(repo: Path, *args: str, boundary: str = "git") -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise IdentityFailure(boundary, "Git is required but was not found") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = f"git {args[0]} exited {completed.returncode}"
        raise IdentityFailure(boundary, detail)
    return completed.stdout


def _resolve_commit(repo: Path, revision: str) -> str:
    output = _git(
        repo,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{revision}^{{commit}}",
        boundary="git-revision",
    )
    commit = output.decode("ascii", errors="strict").strip()
    if not FULL_OBJECT_ID.fullmatch(commit):
        raise IdentityFailure(
            "git-revision", f"Git returned a non-full commit identity: {commit!r}"
        )
    return commit


def _parse_ls_tree(data: bytes, boundary: str) -> list[tuple[str, str, str, str]]:
    parsed: list[tuple[str, str, str, str]] = []
    for record in data.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise IdentityFailure(boundary, "Git returned an invalid tree record") from exc
        parsed.append((mode, kind, object_id, path))
    return parsed


def _validate_skill_name(name: str) -> None:
    if not SKILL_NAME.fullmatch(name):
        raise IdentityFailure("skill-name", f"unsafe skill name: {name!r}")


def _github_part_ok(value: str) -> bool:
    return bool(
        value
        and not value.startswith(".")
        and not value.endswith(".")
        and all(character.isascii() and (character.isalnum() or character in "_.-") for character in value)
    )


def _canonical_github_source(source: str) -> str | None:
    prefix = "https://github.com/"
    if not source.startswith(prefix):
        return None
    path = source[len(prefix) :]
    if path.count("/") != 1:
        return None
    owner, declared_repo = path.split("/", 1)
    repo = declared_repo
    while repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not _github_part_ok(owner) or not _github_part_ok(repo):
        return None
    return f"{prefix}{owner}/{repo}.git"


def _skill_names(repo: Path, commit: str, requested: list[str]) -> list[str]:
    if requested:
        for name in requested:
            _validate_skill_name(name)
        return sorted(set(requested))

    records = _parse_ls_tree(
        _git(repo, "ls-tree", "-z", f"{commit}:skills", boundary="git-skills"),
        "git-skills",
    )
    names = []
    for mode, kind, _object_id, name in records:
        if mode == "040000" and kind == "tree":
            _validate_skill_name(name)
            names.append(name)
    if not names:
        raise IdentityFailure("git-skills", "candidate commit has no skills/* trees")
    return sorted(names)


def _add_parent_directories(tree: dict[str, TreeEntry], relative: str) -> None:
    for parent in PurePosixPath(relative).parents:
        if str(parent) == ".":
            break
        tree.setdefault(parent.as_posix(), TreeEntry("directory"))


def _git_skill_tree(repo: Path, commit: str, name: str) -> dict[str, TreeEntry]:
    prefix = f"skills/{name}"
    records = _parse_ls_tree(
        _git(
            repo,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            commit,
            "--",
            prefix,
            boundary="git-payload",
        ),
        "git-payload",
    )
    tree: dict[str, TreeEntry] = {}
    for mode, kind, object_id, path in records:
        if not path.startswith(f"{prefix}/"):
            raise IdentityFailure("git-payload", f"path escaped {prefix}: {path!r}")
        relative = path[len(prefix) + 1 :]
        parts = PurePosixPath(relative).parts
        if not relative or any(part in {"", ".", ".."} for part in parts):
            raise IdentityFailure("git-payload", f"unsafe Git payload path: {path!r}")
        if relative == RECEIPT_NAME:
            raise IdentityFailure(
                "git-payload", f"repository payload contains reserved {RECEIPT_NAME}"
            )
        if kind != "blob" or mode not in {"100644", "100755"}:
            entry_type = "symlink" if mode == "120000" else f"{kind} mode {mode}"
            raise IdentityFailure(
                "git-payload", f"unsupported {entry_type} at {path!r}"
            )
        content = _git(repo, "cat-file", "blob", object_id, boundary="git-payload")
        tree[relative] = TreeEntry(
            "file", 0o755 if mode == "100755" else 0o644, content
        )
        _add_parent_directories(tree, relative)
    if "SKILL.md" not in tree:
        raise IdentityFailure("git-payload", f"{prefix} has no tracked SKILL.md")
    return tree


def _read_receipt(skill_root: Path) -> dict[str, str]:
    path = skill_root / RECEIPT_NAME
    if path.is_symlink():
        raise IdentityFailure("receipt", f"receipt must not be a symlink: {path}")
    if not path.is_file():
        raise IdentityFailure("receipt", f"missing regular-file receipt: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityFailure("receipt", f"invalid receipt {path}: {exc}") from exc
    expected_keys = {"source", "revision", "path"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise IdentityFailure(
            "receipt", "receipt must contain exactly source, revision, and path"
        )
    if any(not isinstance(value[key], str) or not value[key] for key in expected_keys):
        raise IdentityFailure("receipt", "receipt fields must be non-empty strings")
    revision = value["revision"]
    if not FULL_OBJECT_ID.fullmatch(revision):
        raise IdentityFailure("receipt", "receipt revision must be a full Git object ID")
    receipt_path = value["path"]
    if (
        receipt_path.startswith("/")
        or "\\" in receipt_path
        or any(part in {"", ".", ".."} for part in receipt_path.split("/"))
    ):
        raise IdentityFailure("receipt", "receipt path must be normalized and relative")
    return value


def _installed_tree(skill_root: Path) -> dict[str, TreeEntry]:
    if skill_root.is_symlink():
        raise IdentityFailure(
            "installed-payload", f"installed skill root must not be a symlink: {skill_root}"
        )
    if not skill_root.is_dir():
        raise IdentityFailure(
            "installed-payload", f"installed skill directory is missing: {skill_root}"
        )

    tree: dict[str, TreeEntry] = {}

    def walk(directory: Path, relative_dir: PurePosixPath) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise IdentityFailure(
                "installed-payload", f"cannot read installed directory {directory}: {exc}"
            ) from exc
        for child in children:
            relative = relative_dir / child.name
            relative_text = relative.as_posix()
            try:
                relative_text.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise IdentityFailure(
                    "installed-payload", f"installed payload path is not UTF-8: {child!r}"
                ) from exc
            if relative_text == RECEIPT_NAME:
                continue
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise IdentityFailure(
                    "installed-payload", f"cannot inspect {child}: {exc}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise IdentityFailure(
                    "installed-payload", f"installed payload contains symlink: {child}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                tree[relative_text] = TreeEntry("directory")
                walk(child, relative)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise IdentityFailure(
                    "installed-payload", f"installed payload contains special file: {child}"
                )
            try:
                content = child.read_bytes()
            except OSError as exc:
                raise IdentityFailure(
                    "installed-payload", f"cannot read installed file {child}: {exc}"
                ) from exc
            mode = 0o755 if metadata.st_mode & 0o111 else 0o644
            tree[relative_text] = TreeEntry("file", mode, content)
    walk(skill_root, PurePosixPath())
    if tree.get("SKILL.md", TreeEntry("missing")).kind != "file":
        raise IdentityFailure("installed-payload", f"{skill_root} has no regular SKILL.md")
    return tree


def _path_order(value: str) -> tuple[bytes, ...]:
    # Rust's PathBuf ordering compares path components, not the complete encoded
    # string. Preserve that distinction for e.g. `a/b/c` versus `a/b.txt`.
    return tuple(component.encode("utf-8") for component in value.split("/"))


def _tree_digest(tree: dict[str, TreeEntry]) -> str:
    """Encode the same bytes-and-executable-mode tree identity used by Tink v2."""
    digest = hashlib.sha256(b"tink-tree-digest-v2\0")
    for path in sorted(tree, key=_path_order):
        raw_path = path.encode("utf-8")
        entry = tree[path]
        digest.update(len(raw_path).to_bytes(8, "big"))
        digest.update(raw_path)
        if entry.kind == "directory":
            digest.update(b"d")
            continue
        digest.update(b"f")
        digest.update(entry.mode.to_bytes(4, "big"))
        digest.update(len(entry.content).to_bytes(8, "big"))
        digest.update(entry.content)
    return digest.hexdigest()


def _first_tree_difference(
    expected: dict[str, TreeEntry], installed: dict[str, TreeEntry]
) -> str:
    for path in sorted(set(expected) | set(installed), key=_path_order):
        if path not in installed:
            return f"{path!r} is missing from the installed payload"
        if path not in expected:
            return f"{path!r} exists only in the installed payload"
        left, right = expected[path], installed[path]
        if left.kind != right.kind:
            return f"{path!r} has kind {right.kind}, expected {left.kind}"
        if left.kind == "file" and left.mode != right.mode:
            return (
                f"{path!r} executable mode differs "
                f"(installed {right.mode:o}, Git {left.mode:o})"
            )
        if left.kind == "file" and left.content != right.content:
            return f"{path!r} bytes differ from the Git candidate"
    return "tree digests differ"


def verify_release_identity(
    repo: Path,
    installed_root: Path,
    source: str,
    revision: str,
    requested_skills: list[str],
) -> tuple[str, list[SkillIdentity]]:
    if _canonical_github_source(source) != source:
        raise IdentityFailure(
            "expected-source",
            "--source must be a canonical https://github.com/OWNER/REPO.git URL",
        )
    repo = repo.resolve()
    if installed_root.is_symlink():
        raise IdentityFailure(
            "installed-root", f"installed root must not be a symlink: {installed_root}"
        )
    commit = _resolve_commit(repo, revision)
    skills = _skill_names(repo, commit, requested_skills)
    identities: list[SkillIdentity] = []

    for name in skills:
        installed = installed_root / name
        receipt = _read_receipt(installed)
        if receipt["source"] != source:
            raise IdentityFailure(
                "receipt-source",
                f"{name}: expected {source!r}, got {receipt['source']!r}",
            )
        if receipt["revision"].lower() != commit.lower():
            raise IdentityFailure(
                "receipt-revision",
                f"{name}: expected {commit}, got {receipt['revision']}",
            )
        expected_path = f"skills/{name}"
        if receipt["path"] != expected_path:
            raise IdentityFailure(
                "receipt-path",
                f"{name}: expected {expected_path!r}, got {receipt['path']!r}",
            )

        git_tree = _git_skill_tree(repo, commit, name)
        installed_tree = _installed_tree(installed)
        git_digest = _tree_digest(git_tree)
        installed_digest = _tree_digest(installed_tree)
        if git_digest != installed_digest:
            difference = _first_tree_difference(git_tree, installed_tree)
            raise IdentityFailure(
                "payload",
                f"{name}: {difference}; Git sha256:{git_digest}, "
                f"installed sha256:{installed_digest}",
            )
        identities.append(
            SkillIdentity(
                name=name,
                receipt_path=receipt["path"],
                payload_sha256=installed_digest,
            )
        )
    return commit, identities


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="skill name to verify; repeat it, or omit it to verify every skills/* tree",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        commit, identities = verify_release_identity(
            args.repo_root,
            args.installed_root,
            args.source,
            args.revision,
            args.skill,
        )
    except IdentityFailure as exc:
        print(f"Release identity FAILED [{exc.boundary}]: {exc.detail}", file=sys.stderr)
        return 1

    print(f"release_commit={commit}")
    print(f"receipt_source={args.source}")
    for identity in identities:
        print(
            f"skill={identity.name} receipt_path={identity.receipt_path} "
            f"payload_sha256=sha256:{identity.payload_sha256}"
        )
    print(f"release_identity=verified skills={len(identities)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
