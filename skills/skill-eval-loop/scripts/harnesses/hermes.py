"""Hermes CLI harness adapter."""

from __future__ import annotations

from pathlib import Path

from harnesses.base import BaseHarnessAdapter, isolated_home


class HermesAdapter(BaseHarnessAdapter):
    name = "hermes"
    default_executable = "hermes"

    def prepare_environment(self, output_dir: Path) -> tuple[dict[str, str], Path | None]:
        return isolated_home(output_dir, "hermes-home", "HERMES_HOME")

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
        return [executable, "chat", "-q", prompt, "--model", model]
