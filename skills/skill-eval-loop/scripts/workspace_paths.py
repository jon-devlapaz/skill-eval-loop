#!/usr/bin/env python3
"""Resolve generated skill-eval paths outside the active skills tree."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_EVAL_RUNS_ROOT = Path(__file__).resolve().parents[3] / ".eval-runs"


def safe_component(value: object, label: str) -> str:
    component = str(value)
    if (
        component in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", component)
    ):
        raise ValueError(
            f"{label} must be a single safe directory name containing only "
            "letters, numbers, dot, underscore, or hyphen"
        )
    return component


def eval_skill_root(
    skill_name: object,
    *,
    runs_root: Path | None = None,
) -> Path:
    name = safe_component(skill_name, "skill_name")
    return (runs_root or DEFAULT_EVAL_RUNS_ROOT).resolve() / name


def next_iteration(root: Path) -> int:
    if not root.exists():
        return 1
    numbers = []
    for path in root.iterdir():
        match = re.fullmatch(r"iteration-(\d+)", path.name)
        if match and path.is_dir():
            numbers.append(int(match.group(1)))
    return (max(numbers) + 1) if numbers else 1


def default_run_output(
    skill_name: object,
    *,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> Path:
    if run_id is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_id = f"run-{timestamp}-{secrets.token_hex(3)}"
    safe_run_id = safe_component(run_id, "run_id")
    return eval_skill_root(skill_name, runs_root=runs_root) / safe_run_id
