"""Hermes CLI harness adapter."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from harnesses.base import BaseHarnessAdapter, TraceResult, isolated_home


class HermesAdapter(BaseHarnessAdapter):
    name = "hermes"
    default_executable = "hermes"

    def prepare_environment(self, output_dir: Path) -> tuple[dict[str, str], Path | None]:
        env, home = isolated_home(output_dir, "hermes-home", "HERMES_HOME")
        try:
            host = Path.home() / ".hermes"
            for name in ("config.yaml", ".env", "auth.json"):
                source = host / name
                if source.is_file():
                    target = home / name
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
        return [executable, "chat", "-Q", "-q", prompt, "--model", model]

    def parse_trace(
        self,
        trace_path: Path,
        stderr_path: Path,
        skill_name: str = "",
    ) -> dict[str, Any]:
        text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        kept: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("⚠") or stripped.lower().startswith("session_id:"):
                continue
            kept.append(stripped)
        return TraceResult(response="\n".join(kept).strip()).as_dict()
