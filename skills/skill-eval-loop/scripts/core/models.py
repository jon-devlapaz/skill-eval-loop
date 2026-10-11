"""Typed records at task, rubric, and calibration load boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_GRADERS = {
    "regex",
    "not_regex",
    "file_exists",
    "json_equal",
    "response_not_empty",
    "rubric",
}


class CalibrationBindingError(ValueError):
    """A supplied calibration cannot establish valid runner evidence."""


@dataclass(frozen=True)
class Level:
    name: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


@dataclass(frozen=True)
class Dimension:
    name: str
    levels: tuple[Level, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "levels": [level.as_dict() for level in self.levels]}


@dataclass
class Task:
    id: str
    prompt: str
    graders: list[dict[str, Any]]
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self.extra)
        payload.update({"id": self.id, "prompt": self.prompt, "graders": self.graders})
        return payload


@dataclass(frozen=True)
class CalibrationCase:
    id: str
    better: str
    other: str
    human_winner: str
    rationale: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "better": self.better,
            "other": self.other,
            "human_winner": self.human_winner,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class CalibrationSuite:
    version: int
    prompt: str
    dimensions: tuple[Dimension, ...]
    minimum_agreements: int
    cases: tuple[CalibrationCase, ...]
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "prompt": self.prompt,
            "dimensions": [dimension.as_dict() for dimension in self.dimensions],
            "minimum_agreements": self.minimum_agreements,
            "cases": [case.as_dict() for case in self.cases],
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class CalibrationBinding:
    status: str
    path: str
    sha256: str
    fixtures_path: str
    fixtures_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "path": self.path,
            "sha256": self.sha256,
            "fixtures_path": self.fixtures_path,
            "fixtures_sha256": self.fixtures_sha256,
        }
