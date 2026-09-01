"""Cursor Agent CLI harness adapter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from harnesses.base import BaseHarnessAdapter, TraceResult, isolated_home


class CursorAgentAdapter(BaseHarnessAdapter):
    name = "cursor-agent"
    default_executable = "cursor-agent"

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
