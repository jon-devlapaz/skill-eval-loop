"""Codex CLI harness adapter and Codex JSONL trace parsing."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
from typing import Any

from harnesses.base import BaseHarnessAdapter, TraceResult


def trace_value(event: Any, *keys: str) -> Any:
    current = event
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def prepare_run_codex_home(output: Path) -> Path:
    home = output / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    try:
        source = Path.home() / ".codex" / "auth.json"
        if source.is_file():
            target = home / "auth.json"
            shutil.copyfile(source, target)
            target.chmod(0o600)
    except OSError:
        pass
    return home


def parse_trace(path: Path, skill_name: str = "") -> dict[str, Any]:
    observed = TraceResult()
    with path.open(encoding="utf-8") as trace:
        for line in trace:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                observed.actual_model = trace_value(event, "model") or ""
            elif event.get("type") == "thread.started":
                observed.session_id = trace_value(event, "thread_id") or ""
            elif event.get("type") == "item.completed":
                item_type = trace_value(event, "item", "type")
                if item_type == "agent_message":
                    observed.response = str(trace_value(event, "item", "text") or "").strip()
                elif item_type == "command_execution" and skill_name:
                    command = str(trace_value(event, "item", "command") or "")
                    output = str(trace_value(event, "item", "aggregated_output") or "")
                    skill_path = f".agents/skills/{skill_name}/SKILL.md"
                    skill_frontmatter = re.search(
                        rf"(?m)^name:\s*{re.escape(skill_name)}\s*$", output
                    )
                    if skill_path in command and (
                        trace_value(event, "item", "exit_code") == 0
                        or skill_frontmatter is not None
                    ):
                        observed.skill_accessed = True
            elif event.get("type") == "turn.completed":
                input_tokens = trace_value(event, "usage", "input_tokens")
                output_tokens = trace_value(event, "usage", "output_tokens")
                if isinstance(input_tokens, int) and input_tokens >= 0:
                    observed.input_tokens = input_tokens
                if isinstance(output_tokens, int) and output_tokens >= 0:
                    observed.output_tokens = output_tokens
                if observed.input_tokens is not None and observed.output_tokens is not None:
                    observed.total_tokens = observed.input_tokens + observed.output_tokens
            elif event.get("type") == "turn.failed":
                observed.failure_message = str(trace_value(event, "error", "message") or "")
            elif event.get("type") == "error":
                observed.failure_message = str(event.get("message") or "")
    return observed.as_dict()


class CodexAdapter(BaseHarnessAdapter):
    name = "codex"
    default_executable = "codex"

    def prepare_environment(self, output_dir: Path) -> tuple[dict[str, str], Path | None]:
        home = prepare_run_codex_home(output_dir)
        return {"CODEX_HOME": str(home)}, home

    def invocation_env(self, invocation_dir: Path) -> dict[str, str]:
        return {"HOME": str(invocation_dir / "home")}

    def build_command(
        self,
        *,
        executable: str,
        model: str,
        prompt: str,
        workspace: Path,
        role: str,
        timeout_seconds: int,
        skill_name: str = "",
    ) -> list[str]:
        return [
            executable,
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--model",
            model,
            prompt,
        ]

    def parse_trace(
        self,
        trace_path: Path,
        stderr_path: Path,
        skill_name: str = "",
    ) -> dict[str, Any]:
        return parse_trace(trace_path, skill_name)
