#!/usr/bin/env python3
"""Compatibility entry point for agents that invoke the Python script directly."""

from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).with_name("skill_eval_loop.py")
raise SystemExit(subprocess.run([sys.executable, str(SCRIPT), "run", *sys.argv[1:]]).returncode)
