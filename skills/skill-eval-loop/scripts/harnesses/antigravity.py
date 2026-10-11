"""Antigravity (`agy`) CLI harness adapter."""

from __future__ import annotations

from pathlib import Path
import subprocess

from harnesses.base import BaseHarnessAdapter, ModelListing, isolated_home


def parse_models_table(text: str) -> tuple[str, ...]:
    models: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith(
            ("error", "usage", "failed", "warning", "flags")
        ):
            continue
        token = stripped.split()[0].strip()
        if token:
            models.add(token)
    return tuple(sorted(models))


class AntigravityAdapter(BaseHarnessAdapter):
    name = "antigravity"
    default_executable = "agy"

    def list_models(self, executable: str) -> ModelListing:
        try:
            completed = subprocess.run(
                [executable, "models"],
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ModelListing()
        models = parse_models_table(completed.stdout)
        if not models:
            return ModelListing()
        return ModelListing(models=models, source="cli")

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
        return [
            executable,
            "--model",
            model,
            "--dangerously-skip-permissions",
            "--print",
            prompt,
        ]
