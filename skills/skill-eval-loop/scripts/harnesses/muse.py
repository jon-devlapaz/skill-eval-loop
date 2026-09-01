"""Muse CLI harness adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harnesses.base import BaseHarnessAdapter, ModelListing, TraceResult


def muse_catalog_dir() -> Path:
    return Path.home() / ".local" / "share" / "muse" / "model-catalog"


def catalog_model_ids(catalog_dir: Path) -> tuple[str, ...]:
    if not catalog_dir.is_dir():
        return ()
    models: set[str] = set()
    for path in sorted(catalog_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = row.get("model_id")
            if isinstance(model_id, str) and model_id.strip():
                models.add(model_id.strip())
    return tuple(sorted(models))


class MuseAdapter(BaseHarnessAdapter):
    name = "muse"
    default_executable = "muse"

    def list_models(self, executable: str) -> ModelListing:
        models = catalog_model_ids(muse_catalog_dir())
        if not models:
            return ModelListing()
        return ModelListing(models=models, source="local_catalog")

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
            "exec",
            "--json",
            "--workspace",
            str(workspace),
            "--trust-workspace",
            "--disable-approval",
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
        result = TraceResult()
        accumulated_deltas: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                p_type = event.get("payload_type", "")
                payload = event.get("payload", {})
                if p_type == "run.terminal.completed":
                    result.response = payload.get("text", "")
                elif p_type == "run.output.delta":
                    delta = payload.get("text", "")
                    if delta:
                        accumulated_deltas.append(delta)
                if skill_name and skill_name in line:
                    result.skill_accessed = True
            except json.JSONDecodeError:
                continue
        if not result.response and accumulated_deltas:
            result.response = "".join(accumulated_deltas).strip()
        if not result.response:
            result.response = text.strip()
        return result.as_dict()
