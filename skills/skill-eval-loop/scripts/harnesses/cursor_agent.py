"""Cursor Agent CLI harness adapter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from harnesses.base import BaseHarnessAdapter, ModelListing, TraceResult, isolated_home


def looks_like_model_id(token: str) -> bool:
    if token == "auto":
        return True
    if not token or not all(char.isalnum() or char in "._-[]=" for char in token):
        return False
    return any(char in "-._" for char in token) or any(char.isdigit() for char in token)


def parse_cli_model_ids(text: str) -> tuple[str, ...]:
    cleaned = text.strip()
    if not cleaned:
        return ()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None
    models: set[str] = set()
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str) and looks_like_model_id(item.strip()):
                models.add(item.strip())
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("model") or item.get("model_id")
                if isinstance(model_id, str) and looks_like_model_id(model_id.strip()):
                    models.add(model_id.strip())
        return tuple(sorted(models))
    for line in cleaned.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("failed", "error", "usage", "warning", "available", "tip")):
            continue
        token = line.split()[0].strip(",:")
        if looks_like_model_id(token):
            models.add(token)
    return tuple(sorted(models))


class CursorAgentAdapter(BaseHarnessAdapter):
    name = "cursor-agent"
    default_executable = "cursor-agent"

    def list_models(self, executable: str) -> ModelListing:
        try:
            completed = subprocess.run(
                [executable, "--list-models"],
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ModelListing()
        models = parse_cli_model_ids(completed.stdout)
        if not models:
            return ModelListing()
        return ModelListing(models=models, source="cli")

    def prepare_environment(self, output_dir: Path) -> tuple[dict[str, str], Path | None]:
        env, home = isolated_home(output_dir, "cursor-home", "CURSOR_CONFIG_DIR")
        try:
            sources = [
                Path.home() / ".config" / "cursor" / "cli-config.json",
                Path.home() / ".cursor" / "agent-cli-state.json",
                Path.home() / ".cursor" / "cli-config.json",
            ]
            for source in sources:
                if source.is_file():
                    target = home / source.name
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
        except OSError:
            pass
        return env, home

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
            "--print",
            "--force",
            "--trust",
            "--output-format",
            "json",
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
        text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        result = TraceResult(response=text.strip())
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                if data.get("is_error"):
                    result.failure_message = str(
                        data.get("result") or data.get("error") or "harness reported an error"
                    ).strip()
                    result.response = ""
                    return result.as_dict()
                result.response = str(
                    data.get("result", data.get("text", data.get("output", text)))
                ).strip()
                result.actual_model = str(data.get("model", ""))
                usage = data.get("usage", {})
                if isinstance(usage, dict):
                    in_tok = (
                        usage.get("prompt_tokens")
                        or usage.get("input_tokens")
                        or usage.get("inputTokens")
                    )
                    out_tok = (
                        usage.get("completion_tokens")
                        or usage.get("output_tokens")
                        or usage.get("outputTokens")
                    )
                    if isinstance(in_tok, int):
                        result.input_tokens = in_tok
                    if isinstance(out_tok, int):
                        result.output_tokens = out_tok
                    if result.input_tokens is not None and result.output_tokens is not None:
                        result.total_tokens = result.input_tokens + result.output_tokens
        except json.JSONDecodeError:
            pass
        return result.as_dict()
