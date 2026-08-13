#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
skill_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
exec "$script_dir/skill-eval-loop" healthcheck --skill-dir "$skill_dir" "$@"
