#!/usr/bin/env python3
"""Select a headless evaluator runtime or an optional Herdr observer."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from herdr_runtime import HerdrEvalRun, herdr_plan
from process_control import CapturedKeyboardInterrupt, run_captured


OBSERVERS = {"headless", "herdr"}


class HeadlessEvalRun:
    """Run model processes directly while preserving the evaluator artifacts."""

    observer = "headless"
    workspace_id = ""
    workspace_label = ""

    def run_captured(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float,
        pane_role: str,
        title: str,
        trace_path: Path,
        stderr_path: Path,
    ):
        del pane_role, title
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed, timed_out = run_captured(
                command,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        except CapturedKeyboardInterrupt as exc:
            trace_path.write_text(exc.completed.stdout, encoding="utf-8")
            stderr_path.write_text(exc.completed.stderr, encoding="utf-8")
            raise
        trace_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        return completed, timed_out

    def finish(self, *, status: str, summary: str, artifact_path: Path) -> None:
        del status, summary, artifact_path

    def cancel_active(self) -> None:
        return None


def observer_plan(observer: str, skill_name: str, output_dir: Path) -> dict:
    if observer not in OBSERVERS:
        raise ValueError(f"observer must be one of {sorted(OBSERVERS)}")
    if observer == "herdr":
        return {"kind": "herdr", **herdr_plan(skill_name, output_dir)}
    return {
        "kind": "headless",
        "required_environment": None,
        "artifacts_observable": True,
    }


def require_observer_environment(observer: str) -> None:
    if observer == "herdr":
        HerdrEvalRun.require_environment()
    elif observer != "headless":
        raise ValueError(f"observer must be one of {sorted(OBSERVERS)}")


def start_eval_run(
    observer: str,
    *,
    skill_name: str,
    output_dir: Path,
    cwd: Path,
):
    if observer == "herdr":
        run = HerdrEvalRun.start(
            skill_name=skill_name,
            output_dir=output_dir,
            cwd=cwd,
        )
        run.observer = "herdr"
        return run
    if observer == "headless":
        return HeadlessEvalRun()
    raise ValueError(f"observer must be one of {sorted(OBSERVERS)}")
