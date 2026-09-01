"""Pi CLI harness adapter."""

from __future__ import annotations

from pathlib import Path
import subprocess

from harnesses.base import BaseHarnessAdapter, ModelListing


def parse_list_models_table(text: str) -> tuple[str, ...]:
    models: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("provider"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        provider, model_id = parts[0], parts[1]
        if provider and model_id:
            models.add(f"{provider}/{model_id}")
    return tuple(sorted(models))


class PiAdapter(BaseHarnessAdapter):
    name = "pi"
    default_executable = "pi"

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
        models = parse_list_models_table(completed.stdout)
        if not models:
            return ModelListing()
        return ModelListing(models=models, source="cli")

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
        return [executable, "--print", "--model", model, "--", prompt]
