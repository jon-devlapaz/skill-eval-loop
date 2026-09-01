"""Antigravity CLI harness adapter."""

from __future__ import annotations

from pathlib import Path

from harnesses.base import BaseHarnessAdapter, isolated_home


class AntigravityAdapter(BaseHarnessAdapter):
    name = "antigravity"
    default_executable = "agy"

    def prepare_environment(self, output_dir: Path) -> tuple[dict[str, str], Path | None]:
        return isolated_home(output_dir, "agy-home", "GEMINI_HOME")

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
        return [executable, "--headless", "-p", prompt, "--model", model]
