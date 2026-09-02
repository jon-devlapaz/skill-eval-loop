"""Custom-script harness adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harnesses.base import BaseHarnessAdapter, TraceResult, discover_executable


class ScriptAdapter(BaseHarnessAdapter):
    name = "script"
    default_executable = ""

    def resolve(self, executable: str | None) -> tuple[str, str]:
        if not executable:
            raise ValueError("harness-bin is required for script harness")
        resolved = discover_executable(executable)
        if resolved is None:
            raise ValueError(f"script executable not found: {executable}")
        return resolved, "custom-script 1.0"

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
            "--model",
            model,
            "--role",
            role,
            "--prompt",
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
                    data.get("response", data.get("result", data.get("text", text)))
                ).strip()
                result.actual_model = str(data.get("model", ""))
                result.skill_accessed = bool(data.get("skill_accessed", False))
                in_tok = data.get("input_tokens")
                out_tok = data.get("output_tokens")
                if isinstance(in_tok, int):
                    result.input_tokens = in_tok
                if isinstance(out_tok, int):
                    result.output_tokens = out_tok
                if result.input_tokens is not None and result.output_tokens is not None:
                    result.total_tokens = result.input_tokens + result.output_tokens
        except json.JSONDecodeError:
            pass
        return result.as_dict()
