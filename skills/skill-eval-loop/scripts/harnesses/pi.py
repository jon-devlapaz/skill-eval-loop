"""Pi CLI harness adapter."""

from __future__ import annotations

from pathlib import Path

from harnesses.base import BaseHarnessAdapter


class PiAdapter(BaseHarnessAdapter):
    name = "pi"
    default_executable = "pi"

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
        return [executable, "-p", prompt, "--model", model]
