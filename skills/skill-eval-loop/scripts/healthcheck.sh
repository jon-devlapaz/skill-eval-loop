#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile "$skill_dir"/scripts/*.py
python3 "$skill_dir/scripts/audit_suite.py" --help >/dev/null
python3 "$skill_dir/scripts/run_skill_eval.py" --help >/dev/null
python3 "$skill_dir/scripts/aggregate_benchmark.py" --help >/dev/null
python3 -m unittest discover -s "$skill_dir/tests" -v
