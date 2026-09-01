"""Claude Code CLI harness adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harnesses.base import BaseHarnessAdapter, TraceResult, isolated_home


class ClaudeAdapter(BaseHarnessAdapter):
    name = "claude"
    default_executable = "claude"

    def prepare_environment(self, output_dir: Path) -> tuple[dict[str, str], Path | None]:
        return isolated_home(output_dir, "claude-home", "CLAUDE_CONFIG_DIR")

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
                    data.get("result", data.get("text", data.get("response", text)))
                ).strip()
                result.actual_model = str(data.get("model", ""))
                usage = data.get("usage", {})
                if isinstance(usage, dict):
                    in_tok = usage.get("input_tokens", usage.get("prompt_tokens"))
                    out_tok = usage.get("output_tokens", usage.get("completion_tokens"))
                    if isinstance(in_tok, int):
                        result.input_tokens = in_tok
                    if isinstance(out_tok, int):
                        result.output_tokens = out_tok
                    if result.input_tokens is not None and result.output_tokens is not None:
                        result.total_tokens = result.input_tokens + result.output_tokens
        except json.JSONDecodeError:
            pass
        return result.as_dict()
