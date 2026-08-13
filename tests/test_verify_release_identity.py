"""Tests for the Git-to-Tink release identity verifier."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "verify_release_identity.py"
SOURCE = "https://github.com/example/tink-skills.git"


class ReleaseIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.installed_root = self.root / "project" / ".agents" / "skills"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _write_skill(self, name: str = "demo") -> Path:
        skill = self.repo / "skills" / name
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        executable = skill / "scripts" / "run.sh"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        return skill

    def _commit(self, message: str) -> str:
        self._git("add", ".")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _install(self, revision: str, name: str = "demo", source: str = SOURCE) -> Path:
        installed = self.installed_root / name
        installed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.repo / "skills" / name, installed)
        receipt = {
            "source": source,
            "revision": revision,
            "path": f"skills/{name}",
        }
        (installed / ".tink-source.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        return installed

    def _run(
        self, revision: str, *extra: str, source: str = SOURCE
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(self.repo),
                "--installed-root",
                str(self.installed_root),
                "--source",
                source,
                "--revision",
                revision,
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_matching_receipt_and_payload_are_verified_deterministically(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        self._install(revision)

        first = self._run(revision)
        second = self._run(revision)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn(f"release_commit={revision}", first.stdout)
        self.assertIn(f"receipt_source={SOURCE}", first.stdout)
        self.assertRegex(first.stdout, r"payload_sha256=sha256:[0-9a-f]{64}")
        self.assertIn("release_identity=verified skills=1", first.stdout)

    def test_uppercase_receipt_revision_names_the_same_git_object(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        self._install(revision.upper())

        result = self._run(revision)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"release_commit={revision}", result.stdout)

    def test_ancestor_receipt_fails_even_when_skill_bytes_are_unchanged(self) -> None:
        self._write_skill()
        ancestor = self._commit("skill")
        (self.repo / "README.md").write_text("later commit\n", encoding="utf-8")
        candidate = self._commit("candidate metadata")
        self._install(ancestor)

        result = self._run(candidate)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [receipt-revision]", result.stderr)
        self.assertIn(ancestor, result.stderr)
        self.assertNotIn("release_identity=verified", result.stdout)

    def test_wrong_receipt_source_fails_at_source_boundary(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        self._install(revision, source="https://github.com/attacker/fork.git")

        result = self._run(revision)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [receipt-source]", result.stderr)

    def test_noncanonical_expected_source_is_rejected(self) -> None:
        source = "https://github.com/example/tink-skills.git.git"
        self._write_skill()
        revision = self._commit("candidate")
        self._install(revision, source=source)

        result = self._run(revision, source=source)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [expected-source]", result.stderr)

    def test_payload_byte_drift_reports_the_changed_path(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        installed = self._install(revision)
        (installed / "SKILL.md").write_text("changed\n", encoding="utf-8")

        result = self._run(revision)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [payload]", result.stderr)
        self.assertIn("'SKILL.md' bytes differ", result.stderr)
        self.assertIn("Git sha256:", result.stderr)
        self.assertIn("installed sha256:", result.stderr)

    def test_executable_mode_drift_is_part_of_payload_identity(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        installed = self._install(revision)
        (installed / "scripts" / "run.sh").chmod(0o644)

        result = self._run(revision)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [payload]", result.stderr)
        self.assertIn("executable mode differs", result.stderr)

    def test_extra_empty_directory_is_payload_drift(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        installed = self._install(revision)
        (installed / "unexpected").mkdir()

        result = self._run(revision)

        self.assertEqual(result.returncode, 1)
        self.assertIn("'unexpected' exists only", result.stderr)

    def test_wrong_receipt_path_fails_before_payload_comparison(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        installed = self._install(revision)
        receipt_path = installed / ".tink-source.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["path"] = "skills/other"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        result = self._run(revision)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [receipt-path]", result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_installed_symlink_is_rejected(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        installed = self._install(revision)
        os.symlink(installed / "SKILL.md", installed / "alias.md")

        result = self._run(revision)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [installed-payload]", result.stderr)
        self.assertIn("symlink", result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_installed_root_is_rejected(self) -> None:
        self._write_skill()
        revision = self._commit("candidate")
        self._install(revision)
        real_root = self.installed_root
        link_root = self.root / "linked-skills"
        os.symlink(real_root, link_root)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(self.repo),
                "--installed-root",
                str(link_root),
                "--source",
                SOURCE,
                "--revision",
                revision,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [installed-root]", result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_git_symlink_is_rejected(self) -> None:
        self._write_skill()
        os.symlink("SKILL.md", self.repo / "skills" / "demo" / "alias.md")
        revision = self._commit("candidate with symlink")
        installed = self._install(revision)
        (installed / "alias.md").unlink()

        result = self._run(revision)

        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED [git-payload]", result.stderr)
        self.assertIn("symlink", result.stderr)

    def test_uncommitted_worktree_drift_does_not_change_git_identity(self) -> None:
        skill = self._write_skill()
        revision = self._commit("candidate")
        self._install(revision)
        (skill / "SKILL.md").write_text("dirty worktree\n", encoding="utf-8")

        result = self._run(revision)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release_identity=verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
