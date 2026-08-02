#!/usr/bin/env python3
"""Run one captured command with a process-group timeout."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


class CapturedKeyboardInterrupt(KeyboardInterrupt):
    def __init__(self, completed: subprocess.CompletedProcess[str]) -> None:
        super().__init__("captured command interrupted")
        self.completed = completed


def run_captured(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float,
    termination_grace_seconds: float = 1.0,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        timed_out = False
    except KeyboardInterrupt:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=termination_grace_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        raise CapturedKeyboardInterrupt(
            subprocess.CompletedProcess(
                args=list(command),
                returncode=130,
                stdout=stdout,
                stderr=stderr + "\nInterrupted by user.\n",
            )
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(
                timeout=termination_grace_seconds
            )
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        stderr = (
            stderr
            + f"\nTimed out after {timeout_seconds:g} seconds.\n"
        )
    return (
        subprocess.CompletedProcess(
            args=list(command),
            returncode=124 if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
        ),
        timed_out,
    )
