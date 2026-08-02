#!/usr/bin/env python3
"""Build and inspect sealed harness invocations for paired skill evaluations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = (
    "Work only inside the current workspace. Complete the user's task with "
    "the available capabilities."
)
PAYLOAD_EXCLUDES = {"evals", "tests", "__pycache__", ".DS_Store"}
TOOL_PROFILES = {
    "no_tools": [],
    "read_only": ["read", "grep", "find", "ls"],
    "read_write": ["read", "write"],
    "coding": ["read", "write", "edit", "bash", "grep", "find", "ls"],
}
HARNESS_NAMES = ("hermes", "claude-code", "codex", "pi")
HARNESS_EXECUTABLES = {
    "hermes": "hermes",
    "claude-code": "claude",
    "codex": "codex",
    "pi": "pi",
}


@dataclass(frozen=True)
class RuntimeInvocation:
    command: list[str]
    env: dict[str, str]
    exposed_tools: list[str]
    available_skills: list[str]
    installed_skill_path: Path | None
    skill_activation: str
    tool_enforcement: str


def validate_pinned_model(model: str) -> None:
    normalized = model.strip().lower()
    if not normalized or normalized in {"auto", "default"} or "latest" in normalized:
        raise ValueError("use an exact pinned model id, not a moving alias")


def model_matches(requested: str, actual: object) -> bool:
    if not isinstance(actual, str) or not actual:
        return False
    requested_leaf = requested.rsplit("/", 1)[-1].lower()
    actual_leaf = actual.rsplit("/", 1)[-1].lower()
    return requested_leaf == actual_leaf


def resolve_harness(
    harness: str,
    executable: str | None = None,
) -> tuple[str, str]:
    if harness not in HARNESS_NAMES:
        raise ValueError(f"harness must be one of {list(HARNESS_NAMES)}")
    requested = executable or HARNESS_EXECUTABLES[harness]
    resolved = shutil.which(requested)
    if not resolved:
        raise FileNotFoundError(f"{harness} executable not found: {requested}")
    completed = subprocess.run(
        [resolved, "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    version = completed.stdout.strip()
    if not version:
        raise RuntimeError(f"{harness} returned an empty version")
    return resolved, version


def resolve_pi(executable: str | None = None) -> tuple[str, str]:
    """Compatibility wrapper for existing callers."""
    return resolve_harness("pi", executable)


def _payload_files(source: Path) -> list[Path]:
    if source.is_symlink():
        raise ValueError(f"skill payload root must not be a symlink: {source}")
    source = source.resolve()
    files: list[Path] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlinked skill payload entry is not allowed: {path}")
        relative = path.relative_to(source)
        if path.suffix == ".pyc" or any(
            part in PAYLOAD_EXCLUDES for part in relative.parts
        ):
            continue
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(source)
        except ValueError as exc:
            raise ValueError(
                f"skill payload entry resolves outside its root: {path}"
            ) from exc
        files.append(path)
    return sorted(files)


def skill_payload_sha256(source: Path) -> str:
    source = source.resolve()
    digest = hashlib.sha256()
    for path in _payload_files(source):
        digest.update(path.relative_to(source).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(b"x" if path.stat().st_mode & 0o111 else b"-")
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _install_skill_payload(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True)
    source = source.resolve()
    for path in _payload_files(source):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    if not (destination / "SKILL.md").is_file():
        raise FileNotFoundError(f"installed payload has no SKILL.md: {destination}")
    return destination


def _isolated_codex_env(condition_dir: Path) -> dict[str, str]:
    """Isolate Codex configuration/skills while retaining login credentials."""
    env = os.environ.copy()
    source_codex_home = Path(
        env.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    isolated_home = condition_dir / "harness-home"
    isolated_codex_home = condition_dir / "codex-home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    isolated_codex_home.mkdir(parents=True, exist_ok=True)
    auth = source_codex_home / "auth.json"
    isolated_auth = isolated_codex_home / "auth.json"
    if auth.is_file() and not isolated_auth.exists():
        isolated_auth.symlink_to(auth)
    env["HOME"] = str(isolated_home)
    env["CODEX_HOME"] = str(isolated_codex_home)
    return env


def build_invocation(
    *,
    harness: str = "pi",
    executable: str,
    condition: str,
    condition_dir: Path,
    skill_path: Path,
    prompt: str,
    model: str,
    tool_profile: str,
    activation_mode: str = "forced",
) -> RuntimeInvocation:
    if harness not in HARNESS_NAMES:
        raise ValueError(f"harness must be one of {list(HARNESS_NAMES)}")
    if condition not in {"with_skill", "without_skill"}:
        raise ValueError(f"unsupported condition: {condition}")
    if tool_profile not in TOOL_PROFILES:
        raise ValueError(f"unsupported tool profile: {tool_profile}")
    if activation_mode not in {"forced", "autonomous"}:
        raise ValueError(f"unsupported activation mode: {activation_mode}")

    tools = TOOL_PROFILES[tool_profile]
    installed_skill_path = None
    available_skills: list[str] = []
    if condition == "with_skill":
        destinations = {
            "pi": condition_dir / "installed-skill" / skill_path.name,
            "hermes": condition_dir / "installed-skill" / skill_path.name,
            "claude-code": condition_dir
            / "workspace"
            / ".claude"
            / "skills"
            / skill_path.name,
            "codex": condition_dir
            / "workspace"
            / ".agents"
            / "skills"
            / skill_path.name,
        }
        installed_skill_path = _install_skill_payload(
            skill_path,
            destinations[harness],
        )
        available_skills.append(skill_path.name)

    forced = condition == "with_skill" and activation_mode == "forced"
    env = os.environ.copy()
    if harness == "pi":
        tool_enforcement = "exact_cli_allowlist"
        command = [
            executable,
            "--print",
            "--mode",
            "json",
            "--no-session",
            "--no-skills",
            "--no-extensions",
            "--no-prompt-templates",
            "--no-context-files",
            "--approve",
            "--model",
            model,
            "--append-system-prompt",
            SYSTEM_PROMPT,
        ]
        if tools:
            command.extend(["--tools", ",".join(tools)])
        else:
            command.append("--no-tools")
        if installed_skill_path:
            command.extend(["--skill", str(installed_skill_path)])
        command.append(f"/skill:{skill_path.name} {prompt}" if forced else prompt)
    elif harness == "claude-code":
        tool_enforcement = "exact_cli_allowlist"
        claude_tools = {
            "no_tools": "",
            "read_only": "Read,Grep,Glob",
            "read_write": "Read,Write",
            "coding": "Read,Write,Edit,Bash,Grep,Glob",
        }[tool_profile]
        command = [
            executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--no-session-persistence",
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--tools",
            claude_tools,
            "--permission-mode",
            "bypassPermissions",
            "--append-system-prompt",
            SYSTEM_PROMPT,
            f"/{skill_path.name} {prompt}" if forced else prompt,
        ]
    elif harness == "codex":
        env = _isolated_codex_env(condition_dir)
        tool_enforcement = "sandbox_posture_only"
        sandbox = (
            "read-only"
            if tool_profile in {"no_tools", "read_only"}
            else "workspace-write"
        )
        command = [
            executable,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            sandbox,
            "--model",
            model,
            f"Use the ${skill_path.name} skill. {prompt}" if forced else prompt,
        ]
    else:
        hermes_toolsets = {
            "no_tools": ["file"],
            "read_only": ["file"],
            "read_write": ["file"],
            "coding": ["file", "terminal"],
        }[tool_profile]
        disabled_toolsets = ["file"] if tool_profile == "no_tools" else []
        tool_enforcement = (
            "disabled_toolset"
            if tool_profile == "no_tools"
            else "toolset_posture_only"
        )
        config_path = condition_dir / "hermes-config.yaml"
        external_dirs = (
            [str(installed_skill_path.parent)] if installed_skill_path else []
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "skills": {"external_dirs": external_dirs},
                    "platform_toolsets": {"cli": hermes_toolsets},
                    "agent": {"disabled_toolsets": disabled_toolsets},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env["HERMES_CONFIG"] = str(config_path)
        hermes_prompt = (
            f"Use the {skill_path.name} skill. {prompt}" if forced else prompt
        )
        command = [
            executable,
            "-z",
            f"{SYSTEM_PROMPT}\n\n{hermes_prompt}",
            "--model",
            model,
            "--ignore-rules",
            "--ignore-user-config",
            "--usage-file",
            str(condition_dir / "outputs" / "usage.json"),
        ]
        if installed_skill_path:
            command.extend(["--skills", skill_path.name])
    return RuntimeInvocation(
        command=command,
        env=env,
        exposed_tools=list(tools),
        available_skills=available_skills,
        installed_skill_path=installed_skill_path,
        skill_activation=(
            "forced_command"
            if condition == "with_skill" and activation_mode == "forced"
            else "available_for_autonomous_selection"
            if condition == "with_skill"
            else "none"
        ),
        tool_enforcement=tool_enforcement,
    )


def build_judge_invocation(
    *,
    harness: str,
    executable: str,
    model: str,
    prompt: str,
    run_dir: Path,
) -> RuntimeInvocation:
    """Build a no-skill judge using each harness's strictest tool posture."""
    if harness not in HARNESS_NAMES:
        raise ValueError(f"harness must be one of {list(HARNESS_NAMES)}")
    run_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if harness == "pi":
        tool_enforcement = "exact_cli_allowlist"
        command = [
            executable,
            "--print",
            "--mode",
            "json",
            "--no-session",
            "--no-skills",
            "--no-extensions",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-tools",
            "--model",
            model,
            prompt,
        ]
    elif harness == "claude-code":
        tool_enforcement = "exact_cli_allowlist"
        command = [
            executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--no-session-persistence",
            "--safe-mode",
            "--strict-mcp-config",
            "--tools",
            "",
            prompt,
        ]
    elif harness == "codex":
        env = _isolated_codex_env(run_dir)
        tool_enforcement = "sandbox_posture_only"
        command = [
            executable,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--model",
            model,
            prompt,
        ]
    else:
        tool_enforcement = "disabled_toolset"
        env["HERMES_IGNORE_RULES"] = "1"
        env["HERMES_IGNORE_USER_CONFIG"] = "1"
        config_path = run_dir / "hermes-config.yaml"
        config_path.write_text(
            json.dumps(
                {
                    "platform_toolsets": {"cli": ["file"]},
                    "agent": {"disabled_toolsets": ["file"]},
                    "skills": {"external_dirs": []},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env["HERMES_CONFIG"] = str(config_path)
        command = [
            executable,
            "-z",
            prompt,
            "--model",
            model,
            "--usage-file",
            str(run_dir / "usage.json"),
        ]
    return RuntimeInvocation(
        command=command,
        env=env,
        exposed_tools=[],
        available_skills=[],
        installed_skill_path=None,
        skill_activation="none",
        tool_enforcement=tool_enforcement,
    )


def _strings_for_key(value: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if item_key == key and isinstance(item_value, str):
                found.append(item_value)
            found.extend(_strings_for_key(item_value, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_strings_for_key(item, key))
    return found


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text
            for item in value.values()
            for text in _all_strings(item)
        ]
    if isinstance(value, list):
        return [text for item in value for text in _all_strings(item)]
    return []


def _explicit_skill_call(value: Any, skill_name: str) -> bool:
    if isinstance(value, dict):
        tool_name = value.get("toolName", value.get("name"))
        if (
            isinstance(tool_name, str)
            and tool_name.lower() == "skill"
            and skill_name.lower() in json.dumps(value, sort_keys=True).lower()
        ):
            return True
        return any(_explicit_skill_call(item, skill_name) for item in value.values())
    if isinstance(value, list):
        return any(_explicit_skill_call(item, skill_name) for item in value)
    return False


def _messages(value: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("role") in {"assistant", "user", "toolResult"}:
            messages.append(value)
        for item in value.values():
            messages.extend(_messages(item))
    elif isinstance(value, list):
        for item in value:
            messages.extend(_messages(item))
    return messages


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"]
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    ).strip()


def trace_metadata(
    trace_path: Path,
    skill_name: str,
    installed_skill_path: Path | None = None,
    *,
    harness: str = "pi",
    requested_model: str = "",
    usage_path: Path | None = None,
    codex_home: Path | None = None,
    attestation_trace_path: Path | None = None,
) -> dict[str, object]:
    session_ids: list[str] = []
    models: list[str] = []
    responses: list[str] = []
    usage_records: list[dict[str, Any]] = []
    skill_injection_attested = False
    skill_explicitly_accessed = False
    installed = (
        str(installed_skill_path.resolve()).lower()
        if installed_skill_path
        else ""
    )
    target_suffix = f"/{skill_name.lower()}/skill.md" if skill_name else ""

    raw_trace = trace_path.read_text(encoding="utf-8")
    for line in raw_trace.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        session_ids.extend(_strings_for_key(event, "session_id"))
        if event.get("type") == "session" and isinstance(event.get("id"), str):
            session_ids.append(event["id"])
        if event.get("type") == "thread.started" and isinstance(
            event.get("thread_id"), str
        ):
            session_ids.append(event["thread_id"])
        if (
            event.get("type") == "system"
            and event.get("subtype") == "init"
            and isinstance(event.get("model"), str)
        ):
            models.append(event["model"])
        for message in _messages(event):
            if message.get("role") != "assistant":
                continue
            if isinstance(message.get("model"), str):
                models.append(message["model"])
            response = _message_text(message)
            if response and (not responses or responses[-1] != response):
                responses.append(response)
            if isinstance(message.get("usage"), dict):
                usage_records.append(message["usage"])
        item = event.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            responses.append(item["text"].strip())
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            responses.append(event["result"].strip())
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), dict
        ):
            usage_records.append(event["usage"])
        if skill_name:
            strings = [text.lower() for text in _all_strings(event)]
            if event.get("type") == "system" and event.get("subtype") == "init":
                declared_skills = []
                for key in (
                    "skills",
                    "skill_names",
                    "available_skills",
                    "loaded_skills",
                ):
                    value = event.get(key)
                    if isinstance(value, str):
                        declared_skills.append(value)
                    elif isinstance(value, list):
                        declared_skills.extend(
                            item for item in value if isinstance(item, str)
                        )
                skill_injection_attested = skill_injection_attested or any(
                    value.lower() == skill_name.lower() for value in declared_skills
                )
            if _explicit_skill_call(event, skill_name) or any(
                target_suffix in text
                or (installed and installed in text and "skill.md" in text)
                for text in strings
            ):
                skill_explicitly_accessed = True

    if (
        harness == "codex"
        and attestation_trace_path is None
        and codex_home
        and session_ids
    ):
        session_id = session_ids[-1]
        matches = sorted(
            (codex_home / "sessions").rglob(f"*{session_id}.jsonl")
        )
        if len(matches) == 1:
            attestation_trace_path = matches[0]
    if harness == "codex" and attestation_trace_path:
        if attestation_trace_path.is_file():
            for line in attestation_trace_path.read_text(
                encoding="utf-8"
            ).splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if (
                    event.get("type") == "turn_context"
                    and isinstance(payload.get("model"), str)
                ):
                    models.append(payload["model"])
                if skill_name and event.get("type") == "world_state":
                    state = payload.get("state")
                    host_skills = (
                        state.get("host_skills")
                        if isinstance(state, dict)
                        else None
                    )
                    body = (
                        host_skills.get("body")
                        if isinstance(host_skills, dict)
                        else None
                    )
                    if isinstance(body, str):
                        pattern = (
                            rf"(?m)^-\s+{re.escape(skill_name)}:"
                            rf"[^\n]*\(file:\s+.*?/{re.escape(skill_name)}/"
                            rf"SKILL\.md\)"
                        )
                        skill_injection_attested = (
                            skill_injection_attested
                            or re.search(pattern, body, re.IGNORECASE) is not None
                        )
                if (
                    skill_name
                    and event.get("type") == "response_item"
                    and payload.get("type") == "message"
                    and payload.get("role") == "user"
                ):
                    content = "\n".join(
                        text.lower()
                        for text in _all_strings(payload.get("content"))
                    )
                    if (
                        "<skill>" in content
                        and f"<name>{skill_name.lower()}</name>" in content
                        and installed
                        and f"<path>{installed}/skill.md</path>" in content
                    ):
                        skill_explicitly_accessed = True
                if (
                    skill_name
                    and event.get("type") == "response_item"
                    and payload.get("type")
                    in {"custom_tool_call", "function_call"}
                ):
                    strings = [text.lower() for text in _all_strings(payload)]
                    if _explicit_skill_call(payload, skill_name) or any(
                        target_suffix in text
                        or (installed and installed in text and "skill.md" in text)
                        for text in strings
                    ):
                        skill_explicitly_accessed = True

    if usage_path and usage_path.is_file():
        try:
            usage_report = json.loads(usage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            usage_report = None
        if isinstance(usage_report, dict):
            if isinstance(usage_report.get("model"), str):
                models.append(usage_report["model"])
            if isinstance(usage_report.get("session_id"), str):
                session_ids.append(usage_report["session_id"])
            usage_records.append(usage_report)

    if harness == "hermes" and not responses and raw_trace.strip():
        responses.append(raw_trace.strip())
    usage = usage_records[-1] if usage_records else {}
    model_identities = {
        model.rsplit("/", 1)[-1].lower()
        for model in models
        if model.strip()
    }
    model_attestation_conflict = len(model_identities) > 1
    actual_model = (
        models[-1]
        if models and not model_attestation_conflict
        else ""
    )
    cost: object = usage.get("cost")
    if isinstance(cost, dict):
        cost = cost.get("total")
    return {
        "session_id": session_ids[-1] if session_ids else "",
        "actual_model": actual_model,
        "model_attested": bool(actual_model),
        "model_attestation_conflict": model_attestation_conflict,
        "skill_injection_attested": skill_injection_attested,
        "skill_explicitly_accessed": skill_explicitly_accessed,
        "final_response": responses[-1] if responses else "",
        "input_tokens": usage.get("input", usage.get("input_tokens")),
        "output_tokens": usage.get("output", usage.get("output_tokens")),
        "total_tokens": usage.get(
            "totalTokens",
            (
                usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                if isinstance(usage.get("input_tokens"), int)
                and isinstance(usage.get("output_tokens"), int)
                else None
            ),
        ),
        "cost": (
            cost
            if cost is not None
            else usage.get("total_cost_usd", usage.get("estimated_cost_usd"))
        ),
        "attestation_trace_path": attestation_trace_path,
    }
