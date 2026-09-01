"""Shared path, string, and JSON helpers for evaluator core modules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePath
import sys
import unicodedata
from typing import Any


def absolute_path(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    return path


def required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: must be a non-empty string")
    return value


def relative_workspace_path(value: Any, label: str) -> str:
    path = required_string(value, label)
    parsed = PurePath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
        raise ValueError(f"{label}: must stay inside the trial workspace")
    return path


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def print_json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, indent=2) + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label}: must be an object")
    return value


def retained_file(root: Path, relative: Any, label: str) -> Path:
    value = relative_workspace_path(relative, label)
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}: must stay inside the retained run") from exc
    if not path.is_file():
        raise ValueError(f"{label}: file does not exist")
    return path


def safe_task_id(task_id: str) -> None:
    if not task_id or task_id in {".", ".."} or not task_id[0].isalnum():
        raise ValueError(f'task "{task_id}" field id: must be path-safe')
    if any(
        not (char.isalnum() or unicodedata.category(char).startswith("M") or char in "._-")
        for char in task_id
    ):
        raise ValueError(f'task "{task_id}" field id: must be path-safe')


def normalized_id(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()
