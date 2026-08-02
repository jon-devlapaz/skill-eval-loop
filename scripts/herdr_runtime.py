#!/usr/bin/env python3
"""Run observable skill-eval Pi processes inside a retained Herdr workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


SCRIPT_PATH = Path(__file__).resolve()
TERMINAL_STATES = {"completed", "failed", "cancelled", "invalid"}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _safe_label(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._:-]+", "-", value.strip())
    return normalized.strip("-") or "run"


def herdr_plan(skill_name: str, output_dir: Path) -> dict[str, object]:
    run_id = _safe_label(output_dir.name)
    return {
        "required_for_observer": True,
        "environment_ready": os.environ.get("HERDR_ENV") == "1",
        "workspace_label": f"eval:{_safe_label(skill_name)}:{run_id}",
        "focus_on_start": True,
        "focus_after_start": False,
        "layout": "2x2",
        "panes": {
            "coordinator": "top-left",
            "control": "top-right",
            "with_skill": "bottom-left",
            "judge_results": "bottom-right",
        },
        "workspace_retained": True,
        "notifications": {
            "completed": "done",
            "failed_cancelled_or_blocked": "request",
            "per_trial": False,
        },
    }


def _json_result(completed: subprocess.CompletedProcess[str]) -> dict:
    if not completed.stdout.strip():
        return {}
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Herdr returned invalid JSON for {' '.join(completed.args)}"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
        raise RuntimeError(f"Herdr returned an unexpected response: {value!r}")
    return value["result"]


def _friendly_lines(event: object) -> list[str]:
    if not isinstance(event, dict):
        return []
    lines: list[str] = []
    if event.get("type") == "system" and event.get("subtype") == "init":
        model = event.get("model")
        session = event.get("session_id", event.get("id"))
        detail = " · ".join(str(item) for item in (model, session) if item)
        if detail:
            lines.append(f"Pi ready · {detail}")

    def visit(value: object) -> None:
        if isinstance(value, dict):
            item_type = str(value.get("type", "")).lower()
            name = value.get("toolName", value.get("name"))
            if item_type in {"toolcall", "tool_call", "tool_use"} and name:
                lines.append(f"Tool · {name}")
            if item_type == "text" and isinstance(value.get("text"), str):
                text = value["text"].strip()
                if text:
                    lines.append(f"Assistant\n{text}")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(event.get("message", event.get("content", [])))
    return lines


def _terminate_group(process: subprocess.Popen[str], sig: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return


def _run_job(job_path: Path) -> int:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    command = [str(item) for item in job["command"]]
    cwd = Path(job["cwd"])
    trace_path = Path(job["trace_path"])
    stderr_path = Path(job["stderr_path"])
    result_path = Path(job["result_path"])
    timeout_seconds = float(job["timeout_seconds"])
    title = str(job["title"])
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\033[1;36m{title}\033[0m", flush=True)
    print(f"Model process · {Path(command[0]).name}", flush=True)
    print(f"Artifacts · {trace_path.parent}", flush=True)
    print("─" * 48, flush=True)

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        bufsize=1,
    )
    cancelled = threading.Event()

    def request_cancel(_signum: int, _frame: object) -> None:
        cancelled.set()

    signal.signal(signal.SIGINT, request_cancel)
    signal.signal(signal.SIGTERM, request_cancel)

    def copy_stdout() -> None:
        assert process.stdout is not None
        with trace_path.open("w", encoding="utf-8") as trace:
            for line in process.stdout:
                trace.write(line)
                trace.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    text = line.rstrip()
                    if text:
                        print(f"Pi · {text}", flush=True)
                    continue
                for friendly in _friendly_lines(event):
                    print(friendly, flush=True)

    def copy_stderr() -> None:
        assert process.stderr is not None
        with stderr_path.open("w", encoding="utf-8") as stderr:
            for line in process.stderr:
                stderr.write(line)
                stderr.flush()
                text = line.rstrip()
                if text:
                    print(f"\033[33mPi stderr · {text}\033[0m", flush=True)

    stdout_thread = threading.Thread(target=copy_stdout, daemon=True)
    stderr_thread = threading.Thread(target=copy_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    started = time.monotonic()
    timed_out = False
    was_cancelled = False
    while process.poll() is None:
        if cancelled.is_set():
            was_cancelled = True
            _terminate_group(process, signal.SIGTERM)
            break
        if time.monotonic() - started >= timeout_seconds:
            timed_out = True
            _terminate_group(process, signal.SIGTERM)
            break
        time.sleep(0.05)
    if (timed_out or was_cancelled) and process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _terminate_group(process, signal.SIGKILL)
    return_code = process.wait()
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    if timed_out:
        return_code = 124
    elif was_cancelled:
        return_code = 130
    duration = time.monotonic() - started
    result = {
        "returncode": return_code,
        "timed_out": timed_out,
        "cancelled": was_cancelled,
        "duration_seconds": round(duration, 6),
    }
    status = (
        "timed out"
        if timed_out
        else "cancelled"
        if was_cancelled
        else "completed"
        if return_code == 0
        else f"failed ({return_code})"
    )
    print("─" * 48, flush=True)
    print(f"Status · {status} · {duration:.1f}s", flush=True)
    _write_json(result_path, result)
    return return_code


def _follow_status(status_path: Path) -> int:
    position = 0
    while True:
        if status_path.exists():
            with status_path.open(encoding="utf-8") as stream:
                stream.seek(position)
                while line := stream.readline():
                    text = line.rstrip()
                    if text:
                        print(text, flush=True)
                    if text.startswith("FINAL ·"):
                        return 0
                position = stream.tell()
        time.sleep(0.1)


@dataclass
class PaneSet:
    coordinator: str
    control: str
    with_skill: str
    judge_results: str


class HerdrEvalRun:
    """Own one retained Herdr workspace for a live skill evaluation."""

    def __init__(
        self,
        *,
        workspace_id: str,
        workspace_label: str,
        panes: PaneSet,
        output_dir: Path,
    ) -> None:
        self.workspace_id = workspace_id
        self.workspace_label = workspace_label
        self.panes = panes
        self.output_dir = output_dir
        self.status_path = output_dir / "herdr" / "status.log"
        self.jobs_dir = output_dir / "herdr" / "jobs"
        self._job_number = 0
        self._active_pane: str | None = None

    @staticmethod
    def require_environment() -> None:
        if os.environ.get("HERDR_ENV") != "1":
            raise RuntimeError(
                "live skill evaluations require a Herdr-managed pane (HERDR_ENV=1)"
            )
        if not shutil.which("herdr"):
            raise RuntimeError("live skill evaluations require herdr in PATH")

    @staticmethod
    def _cli(*args: str) -> dict:
        completed = subprocess.run(
            ["herdr", *args],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"Herdr {' '.join(args)} failed: {detail}")
        return _json_result(completed)

    @classmethod
    def start(
        cls,
        *,
        skill_name: str,
        output_dir: Path,
        cwd: Path,
    ) -> "HerdrEvalRun":
        cls.require_environment()
        plan = herdr_plan(skill_name, output_dir)
        label = str(plan["workspace_label"])
        created = cls._cli(
            "workspace",
            "create",
            "--cwd",
            str(cwd),
            "--label",
            label,
            "--no-focus",
        )
        workspace_id = str(created["workspace"]["workspace_id"])
        root = str(created["root_pane"]["pane_id"])
        bottom = str(
            cls._cli(
                "pane",
                "split",
                root,
                "--direction",
                "down",
                "--ratio",
                "0.5",
                "--cwd",
                str(cwd),
                "--no-focus",
            )["pane"]["pane_id"]
        )
        top_right = str(
            cls._cli(
                "pane",
                "split",
                root,
                "--direction",
                "right",
                "--ratio",
                "0.5",
                "--cwd",
                str(cwd),
                "--no-focus",
            )["pane"]["pane_id"]
        )
        bottom_right = str(
            cls._cli(
                "pane",
                "split",
                bottom,
                "--direction",
                "right",
                "--ratio",
                "0.5",
                "--cwd",
                str(cwd),
                "--no-focus",
            )["pane"]["pane_id"]
        )
        panes = PaneSet(
            coordinator=root,
            control=top_right,
            with_skill=bottom,
            judge_results=bottom_right,
        )
        for pane_id, pane_label in (
            (panes.coordinator, "coordinator"),
            (panes.control, "control"),
            (panes.with_skill, "with-skill"),
            (panes.judge_results, "judge-results"),
        ):
            cls._cli("pane", "rename", pane_id, pane_label)
        instance = cls(
            workspace_id=workspace_id,
            workspace_label=label,
            panes=panes,
            output_dir=output_dir,
        )
        instance.status_path.parent.mkdir(parents=True, exist_ok=True)
        instance.status_path.write_text(
            f"Skill Eval · {skill_name}\n"
            f"Workspace · {label}\n"
            f"Artifacts · {output_dir}\n"
            "Execution · controls first, then with-skill\n",
            encoding="utf-8",
        )
        command = shlex.join(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--follow-status",
                str(instance.status_path),
            ]
        )
        cls._cli("pane", "run", panes.coordinator, command)
        cls._cli("workspace", "focus", workspace_id)
        return instance

    def note(self, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with self.status_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{timestamp} · {message}\n")
            stream.flush()

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
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
        if env is not None and dict(env) != dict(os.environ):
            raise ValueError("Herdr pane jobs do not support per-process environment drift")
        pane_id = str(getattr(self.panes, pane_role))
        self._job_number += 1
        job_root = self.jobs_dir / f"{self._job_number:04d}"
        job_path = job_root.with_suffix(".json")
        result_path = job_root.with_suffix(".result.json")
        _write_json(
            job_path,
            {
                "command": list(command),
                "cwd": str(cwd or Path.cwd()),
                "trace_path": str(trace_path),
                "stderr_path": str(stderr_path),
                "result_path": str(result_path),
                "timeout_seconds": timeout_seconds,
                "title": title,
            },
        )
        worker_command = shlex.join(
            [sys.executable, str(SCRIPT_PATH), "--run-job", str(job_path)]
        )
        self.note(f"START · {title}")
        self._active_pane = pane_id
        self._cli("pane", "run", pane_id, worker_command)
        deadline = time.monotonic() + timeout_seconds + 15
        try:
            while not result_path.exists():
                if time.monotonic() >= deadline:
                    self._cli("pane", "send-keys", pane_id, "ctrl+c")
                    raise RuntimeError(f"Herdr worker did not settle: {title}")
                time.sleep(0.05)
        except KeyboardInterrupt:
            self.cancel_active()
            cancellation_deadline = time.monotonic() + 3
            while (
                not result_path.exists()
                and time.monotonic() < cancellation_deadline
            ):
                time.sleep(0.05)
            raise
        finally:
            self._active_pane = None
        result = json.loads(result_path.read_text(encoding="utf-8"))
        stdout = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        stderr = (
            stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        )
        completed = subprocess.CompletedProcess(
            args=list(command),
            returncode=int(result["returncode"]),
            stdout=stdout,
            stderr=stderr,
        )
        self.note(
            f"END · {title} · exit {completed.returncode} · "
            f"{result['duration_seconds']}s"
        )
        time.sleep(0.1)
        return completed, bool(result["timed_out"])

    def cancel_active(self) -> None:
        if self._active_pane:
            self._cli("pane", "send-keys", self._active_pane, "ctrl+c")
            self.note("Cancellation requested for active Pi process")

    def finish(
        self,
        *,
        status: str,
        summary: str,
        artifact_path: Path,
    ) -> None:
        if status not in TERMINAL_STATES:
            raise ValueError(f"unsupported terminal Herdr status: {status}")
        self.note(f"Artifacts · {artifact_path}")
        self.note(f"FINAL · {status} · {summary}")
        summary_path = self.output_dir / "herdr" / "summary.txt"
        summary_path.write_text(
            f"Skill Eval · {status}\n\n{summary}\n\nArtifacts\n{artifact_path}\n",
            encoding="utf-8",
        )
        display_command = shlex.join(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    f"print(Path({str(summary_path)!r}).read_text())"
                ),
            ]
        )
        self._cli("pane", "run", self.panes.judge_results, display_command)
        self._cli(
            "workspace",
            "rename",
            self.workspace_id,
            f"[{status}] {self.workspace_label}",
        )
        sound = "done" if status == "completed" else "request"
        self._cli(
            "notification",
            "show",
            f"Skill eval {status}",
            "--body",
            summary,
            "--position",
            "top-right",
            "--sound",
            sound,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-job", type=Path)
    group.add_argument("--follow-status", type=Path)
    args = parser.parse_args(argv)
    if args.run_job:
        return _run_job(args.run_job)
    return _follow_status(args.follow_status)


if __name__ == "__main__":
    raise SystemExit(main())
