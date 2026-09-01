"""Blinded promotion-review packet models and IO."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
from typing import Any

from core.util import hash_file, write_json


@dataclass(frozen=True)
class ReviewItem:
    id: str
    task_id: str
    trial: int
    rubric_index: int
    prompt: str
    prompt_sha256: str
    dimensions: tuple[str, ...]
    source_report: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "trial": self.trial,
            "rubric_index": self.rubric_index,
            "prompt": self.prompt,
            "prompt_sha256": self.prompt_sha256,
            "dimensions": list(self.dimensions),
            "source_report": self.source_report,
        }


@dataclass
class ReviewPacket:
    run_sha256: str
    tasks_sha256: str
    items: list[ReviewItem]
    required_reviewers: int = 2
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "run_sha256": self.run_sha256,
            "tasks_sha256": self.tasks_sha256,
            "required_reviewers": self.required_reviewers,
            "items": [item.as_dict() for item in self.items],
        }

    def labels_template(self, manifest_sha256: str) -> dict[str, Any]:
        return {
            "version": 1,
            "manifest_sha256": manifest_sha256,
            "reviewer_id": "",
            "labels": [
                {
                    "item_id": item.id,
                    "prompt_sha256": item.prompt_sha256,
                    "winner": "",
                    "rationale": "",
                    "transcript_reviewed": False,
                    "dimensions": [
                        {"name": name, "winner": "", "rationale": ""}
                        for name in item.dimensions
                    ],
                }
                for item in self.items
            ],
        }

    def holdout_template(self) -> dict[str, Any]:
        return {
            "version": 1,
            "tasks_sha256": self.tasks_sha256,
            "custodian_id": "",
            "independent_of_skill_authoring": False,
            "unseen_during_development": False,
            "coverage": {
                "positive": False,
                "negative": False,
                "ambiguous": False,
                "near_tie": False,
                "adversarial": False,
            },
            "rationale": "",
        }

    def write(self, output: Path, copies: list[tuple[Path, Path]]) -> Path:
        output.mkdir(parents=True)
        for source, relative in copies:
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        manifest_path = output / "manifest.json"
        write_json(manifest_path, self.as_dict())
        write_json(output / "labels-template.json", self.labels_template(hash_file(manifest_path)))
        write_json(output / "holdout-attestation-template.json", self.holdout_template())
        return manifest_path


@dataclass
class ReviewerLabels:
    reviewer_id: str
    labels: dict[str, dict[str, Any]]


@dataclass
class Agreement:
    overall_agreements: int
    overall_total: int
    dimension_agreements: int
    dimension_total: int
    disagreements: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": {"agreements": self.overall_agreements, "total": self.overall_total},
            "dimensions": {
                "agreements": self.dimension_agreements,
                "total": self.dimension_total,
            },
        }
