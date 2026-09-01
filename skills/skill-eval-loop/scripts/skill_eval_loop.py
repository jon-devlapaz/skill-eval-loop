#!/usr/bin/env python3
"""Run a paired Codex skill evaluation with retained deterministic evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from core.judging import (  # noqa: E402
    all_rubric_judgments,
    calibration_mapping,
    extract_json_payload as extract_json_payload,
    judge_conditions,
    load_judge_json as load_judge_json,
    mark_judgment_status,
    parse_pairwise,
    pairwise_prompt,
    runner_is_valid,
)
from core.models import (  # noqa: E402
    SUPPORTED_GRADERS,
    CalibrationBinding,
    CalibrationBindingError,
    CalibrationCase,
    CalibrationSuite,
    Dimension,
    Level,
    Task,
)
from core.review import Agreement, ReviewItem, ReviewPacket, ReviewerLabels  # noqa: E402
from core.util import (  # noqa: E402
    absolute_path,
    hash_file,
    load_json_object,
    normalized_id,
    print_json,
    relative_workspace_path,
    required_string,
    retained_file,
    safe_task_id,
    write_json,
)
from harnesses import (  # noqa: E402
    SUPPORTED_HARNESSES,
    get_harness_adapter,
    prepare_run_codex_home,
    resolve_harness,
)
from harnesses.codex import parse_trace as parse_trace  # noqa: E402


MAX_TASK_BYTES = 4 * 1024 * 1024


def error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def progress(message: str) -> None:
    print(f"PROGRESS: {message}", file=sys.stderr, flush=True)


def parse_rubric_dimensions(raw: Any, label: str) -> list[Dimension]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{label} field dimensions: must be a non-empty array")
    dimensions: list[Dimension] = []
    names: set[str] = set()
    for index, value in enumerate(raw):
        dimension_label = f"{label} field dimensions[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{dimension_label}: must be an object")
        name = required_string(value.get("name"), f"{dimension_label} field name")
        if name in names:
            raise ValueError(f'{dimension_label} field name: duplicate value {name!r}')
        levels = value.get("levels")
        if not isinstance(levels, list) or len(levels) < 2:
            raise ValueError(f"{dimension_label} field levels: must contain at least two entries")
        parsed_levels: list[Level] = []
        level_names: set[str] = set()
        for level_index, level in enumerate(levels):
            level_label = f"{dimension_label} field levels[{level_index}]"
            if not isinstance(level, dict):
                raise ValueError(f"{level_label}: must be an object")
            level_name = required_string(level.get("name"), f"{level_label} field name")
            if level_name in level_names:
                raise ValueError(f'{level_label} field name: duplicate value {level_name!r}')
            parsed_levels.append(
                Level(
                    name=level_name,
                    description=required_string(
                        level.get("description"), f"{level_label} field description"
                    ),
                )
            )
            level_names.add(level_name)
        dimensions.append(Dimension(name=name, levels=tuple(parsed_levels)))
        names.add(name)
    return dimensions


def parse_grader(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: must be an object")
    grader_type = required_string(raw.get("type"), f"{label} field type")
    if grader_type not in SUPPORTED_GRADERS:
        raise ValueError(f"{label} field type: unsupported value {grader_type!r}")
    grader = dict(raw)
    if grader_type in {"regex", "not_regex"}:
        pattern = required_string(raw.get("pattern"), f"{label} field pattern")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{label} field pattern: invalid regular expression: {exc}") from exc
        grader["pattern"] = pattern
    elif grader_type in {"file_exists", "json_equal"}:
        grader["path"] = relative_workspace_path(raw.get("path"), f"{label} field path")
        if grader_type == "json_equal" and "expected" not in raw:
            raise ValueError(f"{label} field expected: is required")
    elif grader_type == "rubric":
        grader["dimensions"] = [
            dimension.as_dict() for dimension in parse_rubric_dimensions(raw.get("dimensions"), label)
        ]
    return grader


def load_tasks(path: Path) -> list[Task]:
    tasks: list[Task] = []
    seen: set[str] = set()
    with path.open("rb") as task_file:
        for line_number, line in enumerate(task_file, start=1):
            if len(line) > MAX_TASK_BYTES:
                raise ValueError(f"line {line_number}: exceeds {MAX_TASK_BYTES} bytes")
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"line {line_number}: task must be an object")
            task_id = required_string(raw.get("id"), f"line {line_number} field id")
            safe_task_id(task_id)
            task_key = normalized_id(task_id)
            if task_key in seen:
                raise ValueError(f'task "{task_id}" field id: duplicate value')
            prompt = required_string(raw.get("prompt"), f'task "{task_id}" field prompt')
            raw_graders = raw.get("graders")
            if not isinstance(raw_graders, list) or not raw_graders:
                raise ValueError(f'task "{task_id}" field graders: must be a non-empty array')
            graders = [
                parse_grader(grader, f'task "{task_id}" field graders[{index}]')
                for index, grader in enumerate(raw_graders)
            ]
            if any(grader["type"] == "rubric" for grader in graders) and not any(
                grader["type"] == "response_not_empty" for grader in graders
            ):
                raise ValueError(
                    f'task "{task_id}": rubric graders require a response_not_empty preflight'
                )
            extra = {key: value for key, value in raw.items() if key not in {"id", "prompt", "graders"}}
            tasks.append(Task(id=task_id, prompt=prompt, graders=graders, extra=extra))
            seen.add(task_key)
    if not tasks:
        raise ValueError("tasks: at least one task is required")
    return tasks


REQUIRED_CALIBRATION_CASES = ("known-better", "known-worse", "tie")
INTERVENTION = "injected_skill_instructions"


def load_calibration(path: Path) -> CalibrationSuite:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"calibration: invalid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ValueError("calibration: must be an object")
    if raw.get("version") != 1:
        raise ValueError("calibration field version: must be 1")
    prompt = required_string(raw.get("prompt"), "calibration field prompt")
    dimensions = tuple(parse_rubric_dimensions(raw.get("dimensions"), "calibration"))
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or len(cases_raw) < 3:
        raise ValueError("calibration field cases: must contain at least three entries")
    cases: list[CalibrationCase] = []
    seen: set[str] = set()
    for index, value in enumerate(cases_raw):
        label = f"calibration field cases[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{label}: must be an object")
        case_id = required_string(value.get("id"), f"{label} field id")
        safe_task_id(case_id)
        case_key = normalized_id(case_id)
        if case_key in seen:
            raise ValueError(f"{label} field id: duplicate value {case_id!r}")
        human_winner = required_string(value.get("human_winner"), f"{label} field human_winner")
        if human_winner not in {"better", "other", "tie"}:
            raise ValueError(
                f"{label} field human_winner: must be one of 'better', 'other', or 'tie'"
            )
        cases.append(
            CalibrationCase(
                id=case_id,
                better=required_string(value.get("better"), f"{label} field better"),
                other=required_string(value.get("other"), f"{label} field other"),
                human_winner=human_winner,
                rationale=required_string(value.get("rationale"), f"{label} field rationale"),
            )
        )
        seen.add(case_key)
    missing = [case_id for case_id in REQUIRED_CALIBRATION_CASES if case_id not in seen]
    if missing:
        raise ValueError(
            "calibration field cases: must include known-better, known-worse, and tie"
        )
    minimum = raw.get("minimum_agreements")
    if not isinstance(minimum, int) or minimum < 1 or minimum > len(cases):
        raise ValueError(
            "calibration field minimum_agreements: must be an integer between 1 and the case count"
        )
    return CalibrationSuite(
        version=1,
        prompt=prompt,
        dimensions=dimensions,
        minimum_agreements=minimum,
        cases=tuple(cases),
        sha256=hash_file(path),
    )


def _load_calibration_binding(path: Path, runner_model: str, judge_model: str) -> CalibrationBinding:
    """Validate the retained calibration evidence that a rubric run consumes."""
    try:
        retained = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"calibration: invalid retained JSON: {exc.msg}") from exc
    if not isinstance(retained, dict):
        raise ValueError("calibration: retained evidence must be an object")
    if retained.get("valid") is not True or retained.get("accepted") is not True:
        raise ValueError("calibration: evidence must be valid and accepted")
    configuration = retained.get("configuration")
    if not isinstance(configuration, dict):
        raise ValueError("calibration: retained configuration is required")
    if configuration.get("model") != runner_model:
        raise ValueError("calibration: runner model does not match")
    if configuration.get("judge_model") != judge_model:
        raise ValueError("calibration: judge model does not match")
    fixtures_value = configuration.get("fixtures_path")
    fixtures_hash = configuration.get("fixtures_sha256")
    if not isinstance(fixtures_value, str) or not Path(fixtures_value).is_absolute():
        raise ValueError("calibration: fixtures path must be absolute")
    if not isinstance(fixtures_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", fixtures_hash):
        raise ValueError("calibration: fixtures_sha256 must be a SHA-256 hex digest")
    fixtures = Path(fixtures_value)
    if not fixtures.is_file() or hash_file(fixtures) != fixtures_hash:
        raise ValueError("calibration: fixture path or hash does not match")
    try:
        suite = load_calibration(fixtures)
    except (OSError, ValueError) as exc:
        raise CalibrationBindingError(str(exc)) from exc
    cases = retained.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("calibration: retained cases are required")
    if len(cases) != len(suite.cases) or not all(isinstance(case, dict) for case in cases):
        raise ValueError("calibration: retained cases do not match the fixture")
    if [case.get("id") for case in cases] != [case.id for case in suite.cases]:
        raise ValueError("calibration: retained cases do not match the fixture")
    if retained.get("minimum_agreements") != suite.minimum_agreements:
        raise ValueError("calibration: agreement threshold does not match the fixture")
    orientations: set[str] = set()
    agreement_count = 0
    for case, fixture_case in zip(cases, suite.cases):
        if not isinstance(case, dict) or case.get("status") != "provisional_non_independent":
            raise ValueError("calibration: every case must have a valid judgment")
        mapping = case.get("mapping")
        candidate_a = mapping.get("A") if isinstance(mapping, dict) else None
        candidate_b = mapping.get("B") if isinstance(mapping, dict) else None
        if (
            not isinstance(candidate_a, str)
            or candidate_a not in {"better", "other"}
            or not isinstance(candidate_b, str)
            or candidate_b not in {"better", "other"}
        ):
            raise ValueError("calibration: every case must have a nondegenerate A/B mapping")
        if candidate_a == candidate_b:
            raise ValueError("calibration: every case must map A and B to distinct candidates")
        winner_label = case.get("winner_label")
        if not isinstance(winner_label, str) or winner_label not in {"A", "B", "tie"}:
            raise ValueError("calibration: every case must retain a valid winner label")
        restored_winner = "tie" if winner_label == "tie" else mapping[winner_label]
        if case.get("judge_winner") != restored_winner:
            raise ValueError("calibration: restored judge winner does not match retained evidence")
        if case.get("human_winner") != fixture_case.human_winner:
            raise ValueError("calibration: retained human label does not match the fixture")
        agrees = restored_winner == fixture_case.human_winner
        if case.get("agrees") is not agrees:
            raise ValueError("calibration: retained agreement does not match locked labels")
        orientations.add(candidate_a)
        agreement_count += agrees
    if retained.get("agreements") != agreement_count:
        raise ValueError("calibration: agreement count does not match retained cases")
    if agreement_count < suite.minimum_agreements:
        raise ValueError("calibration: agreement threshold was not met")
    if orientations != {"better", "other"}:
        raise ValueError("calibration: cases must include both A=better and B=better mappings")
    return CalibrationBinding(
        status="accepted",
        path=str(path),
        sha256=hash_file(path),
        fixtures_path=fixtures_value,
        fixtures_sha256=fixtures_hash,
    )


def load_calibration_binding(path: Path, runner_model: str, judge_model: str) -> CalibrationBinding:
    try:
        return _load_calibration_binding(path, runner_model, judge_model)
    except CalibrationBindingError:
        raise
    except (OSError, ValueError) as exc:
        raise CalibrationBindingError(str(exc)) from exc


def payload_files(root: Path) -> list[Path]:
    excluded = {"evals", "tests", "__pycache__", ".DS_Store", ".tink-source.json"}
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"symlinked skill payload entry is not allowed: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files)


def hash_skill(root: Path) -> str:
    digest = hashlib.sha256()
    for path in payload_files(root):
        relative = path.relative_to(root).as_posix().encode()
        mode = b"x" if path.stat().st_mode & 0o111 else b"-"
        digest.update(relative + b"\0" + mode + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def resolve_tasks_path(skill: Path, value: str | None) -> Path:
    if value is not None:
        return absolute_path(value, "tasks")
    owned_suite = skill / "evals" / "tasks.jsonl"
    if not owned_suite.is_file():
        raise ValueError(
            "tasks path is required unless the skill contains evals/tasks.jsonl; "
            "create it with the independent authoring workflow"
        )
    return owned_suite


def reject_tasks_inside_skill(skill: Path, tasks_path: Path, promotion: bool) -> None:
    if not promotion:
        return
    skill_root = skill.resolve()
    resolved_tasks = tasks_path.resolve()
    try:
        resolved_tasks.relative_to(skill_root)
    except ValueError:
        return
    raise ValueError(
        "promotion tasks path must be independently controlled and outside the target skill"
    )


def build_plan(arguments: argparse.Namespace) -> dict[str, Any]:
    target_harness = getattr(arguments, "harness", "codex")
    if target_harness not in SUPPORTED_HARNESSES:
        supported = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise ValueError(f"unsupported harness {target_harness!r}; supported harnesses are: {supported}")
    if not arguments.model or arguments.trials < 1 or arguments.timeout_seconds < 1:
        raise ValueError("model, positive trials, and positive timeout-seconds are required")
    if arguments.promotion and arguments.tasks is None:
        raise ValueError("promotion runs require an explicit independently controlled tasks path")
    if arguments.promotion and arguments.trials < 3:
        raise ValueError("promotion runs require at least 3 trials")
    skill = absolute_path(arguments.skill, "skill")
    output = absolute_path(arguments.output, "output")
    if not (skill / "SKILL.md").is_file():
        raise ValueError("skill path must contain SKILL.md")
    tasks_path = resolve_tasks_path(skill, arguments.tasks)
    reject_tasks_inside_skill(skill, tasks_path, arguments.promotion)
    tasks = [task.as_dict() for task in load_tasks(tasks_path)]
    rubrics = sum(
        1 for task in tasks for grader in task["graders"] if grader["type"] == "rubric"
    )
    if rubrics and not arguments.judge_model:
        raise ValueError("judge-model is required when rubric graders are present")
    if arguments.promotion and rubrics and arguments.calibration is None:
        raise ValueError("promotion runs with rubric graders require accepted calibration")
    calibration: dict[str, Any] | None = None
    if arguments.calibration is not None:
        try:
            calibration_path = absolute_path(arguments.calibration, "calibration")
        except ValueError as exc:
            raise CalibrationBindingError(str(exc)) from exc
        calibration = load_calibration_binding(calibration_path, arguments.model, arguments.judge_model).as_dict()
    judge_harness = getattr(arguments, "judge_harness", None) or target_harness
    executable, version = resolve_harness(target_harness, arguments.harness_bin)
    judge_executable, judge_version = resolve_harness(
        judge_harness, getattr(arguments, "judge_harness_bin", None) or arguments.harness_bin
    )
    paired_trials = len(tasks) * arguments.trials
    target_invocations = paired_trials * 2
    judge_invocations = rubrics * arguments.trials * 3
    return {
        "valid": True,
        "mode": "dry_run",
        "created_artifacts": False,
        "provider_calls": 0,
        "configuration": {
            "skill_path": str(skill),
            "skill_sha256": hash_skill(skill),
            "tasks_path": str(tasks_path),
            "tasks_sha256": hash_file(tasks_path),
            "harness": target_harness,
            "harness_executable": executable,
            "harness_version": version,
            "judge_harness": judge_harness,
            "judge_harness_executable": judge_executable,
            "judge_harness_version": judge_version,
            "model": arguments.model,
            "judge_model": arguments.judge_model,
            "evaluation_role": "promotion" if arguments.promotion else "development",
            "intervention": INTERVENTION,
            "trials": arguments.trials,
            "timeout_seconds": arguments.timeout_seconds,
            "output_dir": str(output),
            "calibration_path": calibration["path"] if calibration else None,
            "calibration_sha256": calibration["sha256"] if calibration else None,
            "calibration_status": calibration["status"] if calibration else "not_run",
            "fixtures_path": calibration["fixtures_path"] if calibration else None,
            "fixtures_sha256": calibration["fixtures_sha256"] if calibration else None,
            "execution": "sequential",
            "condition_order": "alternating_control_first",
            "tool_posture": "read_only",
        },
        "task_snapshot": tasks,
        "counts": {
            "task_count": len(tasks),
            "paired_trials": paired_trials,
            "target_invocations": target_invocations,
            "rubric_grader_count": rubrics,
            "judge_invocations": judge_invocations,
            "total_invocations": target_invocations + judge_invocations,
        },
        "usage": {"tokens": None, "cost": None, "status": "unknown_until_live_run"},
    }


def copy_skill_payload(source: Path, destination: Path) -> None:
    for path in payload_files(source):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(path.stat().st_mode & 0o777)


def discard_runtime_home(home: Path) -> None:
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)


def workspace_target(workspace: Path, relative: str) -> Path:
    root = workspace.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f'path "{relative}" escapes the trial workspace') from exc
    return target


def same_json(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_json(left[key], right[key]) for key in left)
    return left == right


def grade_one(workspace: Path, response: str, grader: dict[str, Any]) -> dict[str, Any]:
    grader_type = grader["type"]
    result: dict[str, Any] = {"type": grader_type, "passed": False, "evidence": ""}
    if grader_type == "response_not_empty":
        result["passed"] = bool(response.strip())
        result["evidence"] = "response is non-empty" if result["passed"] else "response is empty"
        return result
    if grader_type in {"regex", "not_regex"}:
        match = re.search(grader["pattern"], response)
        if grader_type == "regex":
            result["passed"] = match is not None
            result["evidence"] = (
                f'response matched "{match.group(0)}"'
                if match
                else f'response did not match pattern "{grader["pattern"]}"'
            )
        else:
            result["passed"] = match is None
            result["evidence"] = (
                f'response did not match forbidden pattern "{grader["pattern"]}"'
                if not match
                else f'response matched forbidden text "{match.group(0)}"'
            )
        return result
    target = workspace_target(workspace, grader["path"])
    if grader_type == "file_exists":
        result["passed"] = target.is_file()
        result["evidence"] = (
            f'{grader["path"]} exists as a regular file'
            if result["passed"]
            else f'{grader["path"]} is absent'
        )
        return result
    try:
        observed = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["evidence"] = f'{grader["path"]} could not be read as JSON: {exc}'
        return result
    result["passed"] = same_json(observed, grader["expected"])
    result["evidence"] = (
        f'{grader["path"]} equals expected JSON'
        if result["passed"]
        else f'{grader["path"]} differs; observed={json.dumps(observed, separators=(",", ":"))}'
    )
    return result


def grade(task: dict[str, Any], workspace: Path, response: str) -> dict[str, Any]:
    results = [grade_one(workspace, response, grader) for grader in task["graders"] if grader["type"] != "rubric"]
    pending = sum(1 for grader in task["graders"] if grader["type"] == "rubric")
    return {
        "status": "not_scored" if not results else ("pass" if all(result["passed"] for result in results) else "fail"),
        "all_passed": bool(results) and all(result["passed"] for result in results),
        "pending_rubrics": pending,
        "results": results,
    }


class HarnessRuntime:
    """Own the shared harness process, workspace, and evidence lifecycle."""

    def __init__(self, codex_directory: Path, configuration: dict[str, Any]) -> None:
        self.codex_directory = codex_directory
        self.configuration = dict(configuration)
        self.target_harness = self.configuration.get("harness") or "codex"
        self.judge_harness = (
            self.configuration.get("judge_harness")
            or self.configuration.get("harness")
            or "codex"
        )
        self.target_adapter = get_harness_adapter(self.target_harness)
        self.judge_adapter = get_harness_adapter(self.judge_harness)
        base_dir = (
            codex_directory.parent
            if codex_directory.name.endswith("-home")
            else codex_directory
        )
        self.target_env, self.target_home = self.target_adapter.prepare_environment(base_dir)
        self.judge_env, self.judge_home = self.judge_adapter.prepare_environment(base_dir)
        if self.target_harness == "codex":
            self.target_env["CODEX_HOME"] = str(codex_directory)
        if self.judge_harness == "codex":
            self.judge_env["CODEX_HOME"] = str(codex_directory)

    def cleanup(self) -> None:
        self.target_adapter.cleanup_environment(self.target_home)
        self.judge_adapter.cleanup_environment(self.judge_home)

    def _invoke(
        self,
        *,
        invocation_dir: Path,
        workspace: Path,
        prompt: str,
        role: str,
        display_name: str,
        skill_name: str = "",
    ) -> dict[str, Any]:
        target_role = role in {"control", "treatment"}
        adapter = self.target_adapter if target_role else self.judge_adapter
        model = (
            self.configuration["model"]
            if target_role
            else self.configuration["judge_model"]
        )
        executable = (
            self.configuration["harness_executable"]
            if target_role
            else self.configuration.get("judge_harness_executable")
            or self.configuration["harness_executable"]
        )
        invocation_dir.mkdir(parents=True, exist_ok=True)
        (invocation_dir / "home").mkdir(exist_ok=True)
        if not target_role:
            (invocation_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        trace_path = invocation_dir / "trace.jsonl"
        stderr_path = invocation_dir / "stderr.txt"
        response_name = "response.md" if target_role else "response.txt"
        response_path = invocation_dir / response_name
        environment = os.environ.copy()
        environment.pop("OPENAI_API_KEY", None)
        path_parts = environment.get("PATH", "").split(os.pathsep)
        clean_parts = [p for p in path_parts if ".pyenv/shims" not in p]
        if "/opt/homebrew/bin" not in clean_parts:
            clean_parts.insert(0, "/opt/homebrew/bin")
        if "/usr/bin" not in clean_parts:
            clean_parts.append("/usr/bin")
        environment["PATH"] = os.pathsep.join(clean_parts)
        if adapter.name == "codex":
            environment.update(
                {
                    "HOME": str(invocation_dir / "home"),
                }
            )
        adapter_env = self.target_env if target_role else self.judge_env
        environment.update(adapter_env)
        if target_role:
            environment["SKILL_EVAL_SKILL_NAME"] = skill_name
        else:
            environment["SKILL_EVAL_ROLE"] = role
        arguments = adapter.build_command(
            executable=executable,
            model=model,
            prompt=prompt,
            workspace=workspace,
            role=role,
            timeout_seconds=self.configuration["timeout_seconds"],
            skill_name=skill_name,
        )
        started = time.monotonic()
        timed_out = False
        progress(f"starting {display_name}")
        try:
            with trace_path.open("w", encoding="utf-8") as trace, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr:
                completed = subprocess.run(
                    arguments,
                    cwd=workspace,
                    env=environment,
                    stdout=trace,
                    stderr=stderr,
                    timeout=self.configuration["timeout_seconds"],
                    check=False,
                )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = -1
        duration_ms = round((time.monotonic() - started) * 1000)
        observed = adapter.parse_trace(
            trace_path=trace_path,
            stderr_path=stderr_path,
            skill_name=skill_name if role == "treatment" else "",
        )
        response_path.write_text(observed["response"], encoding="utf-8")
        reported_model = observed["actual_model"]
        model_matches = reported_model == model if reported_model else None
        status = "timed_out" if timed_out else ("completed" if exit_code == 0 else "failed")
        failure_reason = (
            "infrastructure_failed"
            if exit_code != 0 and adapter.is_infrastructure_failure(observed.get("failure_message", ""))
            else ""
        )
        progress(f"finished {display_name}: {status} in {duration_ms} ms")
        return {
            "response": observed["response"],
            "skill_accessed": observed["skill_accessed"],
            "failure_reason": failure_reason,
            "timed_out": timed_out,
            "execution": {
                "status": status,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "requested_model": model,
                "trace_reported_model": reported_model,
                "model_identity_source": (
                    "trace_reported" if reported_model else "cli_configured"
                ),
                "model_matches_requested": model_matches,
                "input_tokens": observed["input_tokens"],
                "output_tokens": observed["output_tokens"],
                "total_tokens": observed["total_tokens"],
            },
            "artifact_names": {
                **({"prompt": "prompt.txt"} if not target_role else {}),
                "response": response_name,
                "trace": "trace.jsonl",
                "stderr": "stderr.txt",
            },
        }

    def run_condition(
        self,
        *,
        condition: str,
        pair_dir: Path,
        skill: Path,
        skill_hash: str,
        skill_name: str,
        task: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, bool]]:
        condition_dir = pair_dir / condition
        with tempfile.TemporaryDirectory(prefix=f"skill-eval-{condition}-", ignore_cleanup_errors=True) as temporary:
            workspace = Path(temporary)
            installed_skill = workspace / ".agents" / "skills" / skill_name
            if installed_skill.exists():
                raise ValueError(f"fixture exposes target skill in {condition}")
            isolation = {
                "control_skill_absent": condition == "control",
                "treatment_skill_present": False,
                "treatment_hash_matches": False,
            }
            prompt = task["prompt"]
            if condition == "treatment":
                copy_skill_payload(skill, installed_skill)
                if hash_skill(installed_skill) != skill_hash:
                    raise ValueError("installed skill hash does not match source")
                isolation["treatment_skill_present"] = True
                isolation["treatment_hash_matches"] = True
                skill_instructions = (installed_skill / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                prompt = (
                    "Apply the following skill instructions to the task. The exact hashed "
                    f"skill package is available at .agents/skills/{skill_name}/ for any "
                    "referenced files.\n\n"
                    f"<skill_instructions name=\"{skill_name}\" sha256=\"{skill_hash}\">\n"
                    f"{skill_instructions}\n"
                    "</skill_instructions>\n\n"
                    f"<task>\n{task['prompt']}\n</task>"
                )
            invocation = self._invoke(
                invocation_dir=condition_dir,
                workspace=workspace,
                prompt=prompt,
                role=condition,
                display_name=f"target {task['id']} {condition}",
                skill_name=skill_name,
            )
            deterministic = grade(task, workspace, invocation["response"])
        execution = dict(invocation["execution"])
        reported_model = execution["trace_reported_model"]
        execution["model_requirement_satisfied"] = bool(self.configuration["model"]) and (
            not reported_model or execution["model_matches_requested"]
        )
        execution["failure_reason"] = invocation["failure_reason"]
        return (
            {
                "name": condition,
                "response": invocation["response"],
                "activation": {
                    "status": "observed" if condition == "treatment" else "unknown",
                    "reason": (
                        "skill_instructions_injected"
                        if condition == "treatment"
                        else "no_skill_in_control"
                    ),
                    "trace_skill_read": invocation["skill_accessed"],
                },
                "deterministic_status": deterministic["status"],
                "pending_rubrics": deterministic["pending_rubrics"],
                "graders": deterministic["results"],
                "execution": execution,
                "artifacts": {
                    label: f"{condition}/{name}"
                    for label, name in invocation["artifact_names"].items()
                },
            },
            isolation,
        )

    def invoke_judge(
        self,
        *,
        judge_dir: Path,
        artifact_root: Path,
        prompt: str,
        role: str,
    ) -> tuple[dict[str, Any], str]:
        artifact_prefix = judge_dir.relative_to(artifact_root).as_posix()
        with tempfile.TemporaryDirectory(prefix=f"skill-eval-{role}-", ignore_cleanup_errors=True) as temporary:
            invocation = self._invoke(
                invocation_dir=judge_dir,
                workspace=Path(temporary),
                prompt=prompt,
                role=role,
                display_name=f"{role} {artifact_prefix}",
            )
        execution = invocation["execution"]
        result: dict[str, Any] = {
            "status": "unknown",
            "reason": "",
            "dimensions": [],
            "execution": execution,
            "artifacts": {
                label: f"{artifact_prefix}/{name}"
                for label, name in invocation["artifact_names"].items()
            },
        }
        if invocation["timed_out"]:
            result["reason"] = "timed_out"
        elif execution["exit_code"] != 0:
            result["reason"] = (
                "infrastructure_failed"
                if invocation["failure_reason"] == "infrastructure_failed"
                else "judge_failed"
            )
        elif execution["trace_reported_model"] and not execution["model_matches_requested"]:
            result["reason"] = "model_identity_mismatch"
        return result, invocation["response"]


CodexRuntime = HarnessRuntime


def deterministic_comparison(control: str, treatment: str) -> str:
    if "not_scored" in {control, treatment}:
        return "not_scored"
    return {
        ("pass", "pass"): "both_pass",
        ("fail", "pass"): "treatment_only",
        ("pass", "fail"): "control_only",
        ("fail", "fail"): "both_fail",
    }[(control, treatment)]


def pair_executions(
    conditions: dict[str, dict[str, Any]], pairwise: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    executions = [condition["execution"] for condition in conditions.values()]
    executions.extend(judgment["execution"] for judgment in all_rubric_judgments(conditions))
    executions.extend(judgment["execution"] for judgment in pairwise)
    return executions


def summarize_usage(executions: list[dict[str, Any]]) -> dict[str, Any]:
    invoked = [execution for execution in executions if execution.get("status") != "not_run"]
    measured = [execution for execution in invoked if isinstance(execution.get("total_tokens"), int)]
    return {
        "status": (
            "complete"
            if invoked and len(measured) == len(invoked)
            else "partial" if measured else "unknown"
        ),
        "invocations": len(invoked),
        "measured_invocations": len(measured),
        "duration_ms": sum(
            execution.get("duration_ms", 0)
            for execution in invoked
            if isinstance(execution.get("duration_ms"), int)
        ),
        "input_tokens": sum(execution.get("input_tokens", 0) for execution in measured),
        "output_tokens": sum(execution.get("output_tokens", 0) for execution in measured),
        "total_tokens": sum(execution["total_tokens"] for execution in measured),
        "cost": None,
        "cost_status": "unknown",
    }


def evidence_status(judgments: list[dict[str, Any]]) -> str:
    if not judgments:
        return "not_required"
    if any(judgment["status"] == "unknown" for judgment in judgments):
        return "unknown"
    if any(judgment["status"] == "independent" for judgment in judgments):
        return "independent"
    return "provisional_non_independent"


def rubric_status(conditions: dict[str, dict[str, Any]]) -> str:
    return evidence_status(all_rubric_judgments(conditions))


def pairwise_status(pairwise: list[dict[str, Any]]) -> str:
    return evidence_status(pairwise)


def restored_condition(judgment: dict[str, Any], label: str) -> str:
    if label == "tie":
        return "tie"
    return (judgment.get("mapping") or {}).get(label, "")


def dimension_results(
    conditions: dict[str, dict[str, Any]], pairwise: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for condition_name, condition in conditions.items():
        for judgment in condition.get("rubric_judgments", []):
            if judgment["status"] == "unknown" or not judgment["dimensions"]:
                results.append(
                    {
                        "source": "per_output",
                        "condition": condition_name,
                        "name": "",
                        "status": "unknown",
                        "reason": judgment.get("reason", ""),
                    }
                )
                continue
            for dimension in judgment["dimensions"]:
                results.append(
                    {
                        "source": "per_output",
                        "condition": condition_name,
                        "name": dimension["name"],
                        "status": judgment["status"],
                        "level": dimension["level"],
                        "evidence": dimension["evidence"],
                    }
                )
    for judgment in pairwise:
        if judgment["status"] == "unknown" or not judgment["dimensions"]:
            results.append(
                {
                    "source": "pairwise",
                    "name": "",
                    "status": "unknown",
                    "reason": judgment.get("reason", ""),
                }
            )
            continue
        for dimension in judgment["dimensions"]:
            results.append(
                {
                    "source": "pairwise",
                    "name": dimension["name"],
                    "status": judgment["status"],
                    "winner_label": dimension["winner"],
                    "winner_condition": restored_condition(judgment, dimension["winner"]),
                    "evidence": dimension["evidence"],
                }
            )
    return results


def quality_status_for(rubric: str, pairwise: str, calibration_status: str) -> str:
    if rubric == "not_required":
        return "not_required"
    if calibration_status != "accepted" or rubric == "unknown" or pairwise == "unknown":
        return "unknown"
    if rubric == "independent" or pairwise == "independent":
        return "independent"
    return "provisional_non_independent"


def quality_outcome_for(pairwise: list[dict[str, Any]], quality_status: str) -> str:
    if quality_status == "not_required":
        return "not_judged"
    if quality_status == "unknown":
        return "unknown"
    overall = ""
    inconsistent = False
    for judgment in pairwise:
        winner = judgment.get("winner_condition", "")
        if overall and winner != overall:
            inconsistent = True
        overall = overall or winner
        for dimension in judgment["dimensions"]:
            dimension_winner = restored_condition(judgment, dimension["winner"])
            if winner != "tie" and dimension_winner not in {"tie", winner}:
                inconsistent = True
    if inconsistent:
        return "inconsistent"
    if overall == "tie":
        return "tie"
    return overall


def rollup_quality_status(statuses: list[str]) -> str:
    if any(status == "unknown" for status in statuses):
        return "unknown"
    if any(status == "independent" for status in statuses):
        return "independent"
    if any(status == "provisional_non_independent" for status in statuses):
        return "provisional_non_independent"
    return "not_required"


def live_exit_code(runner_valid: bool, quality_status: str) -> int:
    if not runner_valid:
        return 2
    if quality_status in {"provisional_non_independent", "independent"}:
        return 0
    return 1


def dimension_line(item: dict[str, Any]) -> str:
    if item["source"] == "per_output":
        target = f"{item['condition']} / {item['name'] or 'rubric'}"
        if item["status"] == "unknown":
            return f"{target}: unknown ({item['reason']})"
        return f"{target}: {item['level']}"
    target = f"pairwise / {item['name'] or 'rubric'}"
    if item["status"] == "unknown":
        return f"{target}: unknown ({item['reason']})"
    return f"{target}: {item['winner_label']} ({item['winner_condition']})"


def write_pair_report(
    pair_dir: Path,
    task: dict[str, Any],
    trial: int,
    execution_order: list[str],
    skill_name: str,
    skill_hash: str,
    conditions: dict[str, dict[str, Any]],
    isolation: dict[str, bool],
    pairwise: list[dict[str, Any]],
    calibration_status: str,
    fixtures_sha256: str | None,
) -> tuple[dict[str, Any], Path, Path]:
    control = conditions["control"]
    treatment = conditions["treatment"]
    runner_valid = runner_is_valid(conditions, isolation)
    rubric = rubric_status(conditions)
    pair = pairwise_status(pairwise)
    quality_status = quality_status_for(rubric, pair, calibration_status)
    dimensions = dimension_results(conditions, pairwise)
    report = {
        "runner_valid": runner_valid,
        "intervention": INTERVENTION,
        "task": {"id": task["id"], "prompt": task["prompt"], "graders": task["graders"]},
        "trial": trial,
        "execution_order": execution_order,
        "activation": conditions["treatment"]["activation"],
        "deterministic_comparison": deterministic_comparison(
            control["deterministic_status"], treatment["deterministic_status"]
        ),
        "review_status": "human_transcript_review_required",
        "rubric_status": rubric,
        "pairwise_status": pair,
        "quality_status": quality_status,
        "quality_outcome": quality_outcome_for(pairwise, quality_status),
        "calibration_status": calibration_status,
        "fixtures_sha256": fixtures_sha256,
        "dimension_results": dimensions,
        "usage": summarize_usage(pair_executions(conditions, pairwise)),
        "pairwise": pairwise,
        "skill": {"name": skill_name, "sha256": skill_hash},
        "isolation": {
            "control_skill_absent": isolation["control_skill_absent"],
            "treatment_skill_present": isolation["treatment_skill_present"],
            "treatment_installed_source_hash_match": isolation["treatment_hash_matches"],
        },
        "tool_posture": "read_only",
        "cost": None,
        "cost_status": "unknown",
        "conditions": [control, treatment],
    }
    report_path = pair_dir / "report.json"
    markdown_path = pair_dir / "report.md"
    write_json(report_path, report)
    dimension_lines = (
        [f"- {dimension_line(item)}" for item in dimensions]
        if dimensions
        else ["- Semantic quality was not judged."]
    )
    artifact_links: list[str] = []
    for condition in (control, treatment):
        for label, relative in condition["artifacts"].items():
            artifact_links.append(f"- [{condition['name']} {label}]({relative})")
        for judgment in condition.get("rubric_judgments", []):
            for label, relative in judgment.get("artifacts", {}).items():
                artifact_links.append(f"- [{condition['name']} judge {label}]({relative})")
    for index, judgment in enumerate(pairwise, start=1):
        for label, relative in judgment.get("artifacts", {}).items():
            artifact_links.append(f"- [pairwise {index} {label}]({relative})")
    markdown_path.write_text(
        "\n".join(
            [
                f"# {task['id']} trial {trial}",
                "",
                f"Runner valid: {runner_valid}",
                f"Intervention: {INTERVENTION}",
                f"Activation: {report['activation']['status']} ({report['activation']['reason']})",
                f"Deterministic comparison: {report['deterministic_comparison']}",
                f"Rubric status: {report['rubric_status']}",
                f"Pairwise status: {report['pairwise_status']}",
                f"Quality status: {report['quality_status']}",
                f"Quality outcome: {report['quality_outcome']}",
                f"Calibration: {report['calibration_status']}",
                f"Execution order: {', '.join(execution_order)}",
                "",
                "Dimensions:",
                *dimension_lines,
                "",
                "Artifacts (relative to this report):",
                *artifact_links,
                "",
                "Inspect the JSON report and condition artifacts for authoritative evidence.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return report, report_path, markdown_path


def run_live(plan: dict[str, Any]) -> dict[str, Any]:
    configuration = plan["configuration"]
    skill = Path(configuration["skill_path"])
    tasks_path = Path(configuration["tasks_path"])
    output = Path(configuration["output_dir"])
    tasks = [task.as_dict() for task in load_tasks(tasks_path)]
    if hash_file(tasks_path) != configuration["tasks_sha256"]:
        raise ValueError("tasks changed after dry-run planning")
    if hash_skill(skill) != configuration["skill_sha256"]:
        raise ValueError("skill changed after dry-run planning")
    calibration_status = configuration.get("calibration_status", "not_run")
    fixtures_sha256 = configuration.get("fixtures_sha256")
    if configuration.get("calibration_path") is not None:
        calibration_path = Path(configuration["calibration_path"])
        binding = load_calibration_binding(
            calibration_path, configuration["model"], configuration["judge_model"]
        )
        if binding.sha256 != configuration.get("calibration_sha256"):
            raise CalibrationBindingError("calibration changed after dry-run planning")
        if binding.fixtures_sha256 != configuration.get("fixtures_sha256"):
            raise CalibrationBindingError("calibration fixture changed after dry-run planning")
    if output.exists():
        raise ValueError(f"output directory already exists: {output}")
    skill_name = skill.name
    output.mkdir(parents=True)
    codex_directory = output / "codex-home"
    try:
        codex_directory = prepare_run_codex_home(output)
        runtime = CodexRuntime(codex_directory, configuration)
        write_json(
            output / "config.json",
            {"mode": "live", "configuration": configuration, "counts": plan["counts"]},
        )
        shutil.copyfile(tasks_path, output / "tasks.jsonl")
        result: dict[str, Any] = {
            "valid": True,
            "mode": "live",
            "output_dir": str(output),
            "configuration": configuration,
            "counts": plan["counts"],
            "activation": {"status": "unknown", "reason": "pending"},
            "calibration_status": calibration_status,
            "fixtures_sha256": fixtures_sha256,
            "quality_status": "not_required",
            "pairs": [],
        }
        quality_statuses: list[str] = []
        executions: list[dict[str, Any]] = []
        for task in tasks:
            for trial in range(1, configuration["trials"] + 1):
                pair_dir = output / f"task-{task['id']}" / f"trial-{trial:03d}"
                pair_dir.mkdir(parents=True)
                execution_order = ["control", "treatment"] if trial % 2 else ["treatment", "control"]
                conditions: dict[str, dict[str, Any]] = {}
                isolation = {"control_skill_absent": False, "treatment_skill_present": False, "treatment_hash_matches": False}
                for condition in execution_order:
                    condition_result, current_isolation = runtime.run_condition(
                        condition=condition,
                        pair_dir=pair_dir,
                        skill=skill,
                        skill_hash=configuration["skill_sha256"],
                        skill_name=skill_name,
                        task=task,
                    )
                    conditions[condition] = condition_result
                    executions.append(condition_result["execution"])
                    for key, value in current_isolation.items():
                        isolation[key] = isolation[key] or value
                    if condition_result["execution"]["failure_reason"] == "infrastructure_failed":
                        result["valid"] = False
                        result["quality_status"] = "unknown"
                        result["failure"] = {
                            "reason": "infrastructure_failed",
                            "task_id": task["id"],
                            "trial": trial,
                            "condition": condition,
                        }
                        result["usage"] = summarize_usage(executions)
                        write_json(output / "run.json", result)
                        return result
                pairwise = judge_conditions(
                    runtime=runtime,
                    pair_dir=pair_dir,
                    configuration=configuration,
                    task=task,
                    conditions=conditions,
                    isolation=isolation,
                    trial=trial,
                )
                executions.extend(
                    judgment["execution"] for judgment in all_rubric_judgments(conditions)
                )
                executions.extend(judgment["execution"] for judgment in pairwise)
                report, report_path, markdown_path = write_pair_report(
                    pair_dir,
                    task,
                    trial,
                    execution_order,
                    skill_name,
                    configuration["skill_sha256"],
                    conditions,
                    isolation,
                    pairwise,
                    calibration_status,
                    fixtures_sha256,
                )
                if not report["runner_valid"]:
                    result["valid"] = False
                quality_statuses.append(report["quality_status"])
                result["pairs"].append(
                    {
                        "task_id": task["id"],
                        "trial": trial,
                        "runner_valid": report["runner_valid"],
                        "quality_status": report["quality_status"],
                        "quality_outcome": report["quality_outcome"],
                        "activation": report["activation"],
                        "execution_order": execution_order,
                        "report_json": str(report_path.relative_to(output).as_posix()),
                        "report_markdown": str(markdown_path.relative_to(output).as_posix()),
                    }
                )
        result["quality_status"] = rollup_quality_status(quality_statuses)
        result["usage"] = summarize_usage(executions)
        observed_activations = sum(
            pair["activation"]["status"] == "observed" for pair in result["pairs"]
        )
        if observed_activations == len(result["pairs"]):
            result["activation"] = {
                "status": "observed",
                "reason": "all_treatments_received_skill_instructions",
            }
        elif observed_activations:
            result["activation"] = {
                "status": "partial",
                "reason": "some_treatments_received_skill_instructions",
            }
        else:
            result["activation"] = {
                "status": "unknown",
                "reason": "no_treatment_skill_instructions_delivered",
            }
        write_json(output / "run.json", result)
        return result
    finally:
        discard_runtime_home(codex_directory)


def build_calibration_plan(arguments: argparse.Namespace) -> dict[str, Any]:
    target_harness = getattr(arguments, "harness", "codex")
    if target_harness not in SUPPORTED_HARNESSES:
        supported = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise ValueError(f"unsupported harness {target_harness!r}; supported harnesses are: {supported}")
    if not arguments.model or not arguments.judge_model or arguments.timeout_seconds < 1:
        raise ValueError("model, judge-model, and positive timeout-seconds are required")
    if arguments.judge_model == arguments.model:
        raise ValueError("judge-model must differ from model")
    fixtures = absolute_path(arguments.fixtures, "fixtures")
    output = absolute_path(arguments.output, "output")
    suite = load_calibration(fixtures).as_dict()
    judge_harness = getattr(arguments, "judge_harness", None) or target_harness
    executable, version = resolve_harness(target_harness, arguments.harness_bin)
    judge_executable, judge_version = resolve_harness(
        judge_harness, getattr(arguments, "judge_harness_bin", None) or arguments.harness_bin
    )
    return {
        "valid": True,
        "mode": "dry_run",
        "created_artifacts": False,
        "configuration": {
            "fixtures_path": str(fixtures),
            "fixtures_sha256": suite["sha256"],
            "harness": target_harness,
            "harness_executable": executable,
            "harness_version": version,
            "judge_harness": judge_harness,
            "judge_harness_executable": judge_executable,
            "judge_harness_version": judge_version,
            "model": arguments.model,
            "judge_model": arguments.judge_model,
            "timeout_seconds": arguments.timeout_seconds,
            "output_dir": str(output),
            "tool_posture": "read_only",
        },
        "suite": {
            "version": suite["version"],
            "prompt": suite["prompt"],
            "dimensions": suite["dimensions"],
            "minimum_agreements": suite["minimum_agreements"],
            "cases": [
                {"id": case["id"], "human_winner": case["human_winner"], "rationale": case["rationale"]}
                for case in suite["cases"]
            ],
        },
        "counts": {
            "case_count": len(suite["cases"]),
            "judge_invocations": len(suite["cases"]),
            "total_invocations": len(suite["cases"]),
        },
    }


def run_calibration_case(
    *,
    runtime: HarnessRuntime,
    output: Path,
    suite: dict[str, Any],
    case: dict[str, Any],
    seed: int,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mapping = calibration_mapping(seed)
    candidates = {label: case[slot] for label, slot in mapping.items()}
    result, raw = runtime.invoke_judge(
        judge_dir=output / case["id"],
        artifact_root=output,
        prompt=pairwise_prompt(
            {"prompt": suite["prompt"]},
            candidates,
            {"dimensions": suite["dimensions"]},
        ),
        role="pairwise",
    )
    result["id"] = case["id"]
    result["mapping"] = mapping
    result["human_winner"] = case["human_winner"]
    result["rationale"] = case["rationale"]
    result["judge_winner"] = ""
    result["agrees"] = False
    if result["reason"]:
        return result
    try:
        winner, dimensions = parse_pairwise(raw, {"dimensions": suite["dimensions"]})
    except ValueError:
        result["reason"] = "malformed_output"
        return result
    restored = "tie" if winner == "tie" else mapping[winner]
    result["dimensions"] = dimensions
    result["winner_label"] = winner
    result["judge_winner"] = restored
    result["agrees"] = restored == case["human_winner"]
    return mark_judgment_status(result, configuration)


def run_calibrate(plan: dict[str, Any]) -> dict[str, Any]:
    configuration = plan["configuration"]
    fixtures = Path(configuration["fixtures_path"])
    output = Path(configuration["output_dir"])
    suite = load_calibration(fixtures).as_dict()
    if suite["sha256"] != configuration["fixtures_sha256"]:
        raise ValueError("calibration fixtures changed after dry-run planning")
    if output.exists():
        raise ValueError(f"output directory already exists: {output}")
    output.mkdir(parents=True)
    codex_directory = output / "codex-home"
    try:
        codex_directory = prepare_run_codex_home(output)
        runtime = HarnessRuntime(codex_directory, configuration)
        write_json(
            output / "config.json",
            {"mode": "calibrate", "configuration": configuration, "counts": plan["counts"]},
        )
        result: dict[str, Any] = {
            "valid": True,
            "accepted": False,
            "mode": "calibrate",
            "output_dir": str(output),
            "configuration": configuration,
            "minimum_agreements": suite["minimum_agreements"],
            "agreements": 0,
            "disagreements": [],
            "cases": [],
        }
        for index, case in enumerate(suite["cases"], start=1):
            judged = run_calibration_case(
                runtime=runtime,
                output=output,
                suite=suite,
                case=case,
                seed=index,
                configuration=configuration,
            )
            result["cases"].append(judged)
            if judged["status"] == "unknown":
                result["valid"] = False
                if judged["reason"] == "infrastructure_failed":
                    break
                continue
            if judged["agrees"]:
                result["agreements"] += 1
            else:
                result["disagreements"].append(
                    {
                        "id": case["id"],
                        "human_winner": case["human_winner"],
                        "judge_winner": judged["judge_winner"],
                        "rationale": case["rationale"],
                    }
                )
        result["accepted"] = (
            result["valid"] and result["agreements"] >= suite["minimum_agreements"]
        )
        result["usage"] = summarize_usage([case["execution"] for case in result["cases"]])
        write_json(output / "calibration.json", result)
        return result
    finally:
        discard_runtime_home(codex_directory)


def calibration_exit_code(result: dict[str, Any]) -> int:
    if not result["valid"]:
        return 2
    if result["accepted"]:
        return 0
    return 1


def healthcheck(arguments: argparse.Namespace) -> int:
    root = Path(arguments.skill_dir).resolve() if arguments.skill_dir else Path(__file__).resolve().parents[1]
    required = [
        "SKILL.md",
        "scripts/skill_eval_loop.py",
        "scripts/skill-eval-loop",
        "scripts/harnesses/__init__.py",
        "scripts/core/__init__.py",
        "scripts/core/judging.py",
        "scripts/core/models.py",
        "scripts/core/review.py",
        "scripts/core/util.py",
        "references/promotion-workflow.md",
    ]
    missing = [relative for relative in required if not (root / relative).is_file()]
    print_json(
        {
            "valid": not missing,
            "skill_dir": str(root),
            "commands": [
                "healthcheck",
                "run",
                "calibrate",
                "prepare-review",
                "finalize-review",
            ],
            "errors": [f"{relative} is missing" for relative in missing],
        }
    )
    return 0 if not missing else 1


def run(arguments: argparse.Namespace) -> int:
    plan = build_plan(arguments)
    if arguments.dry_run:
        print_json(plan)
        return 0
    result = run_live(plan)
    print_json(result)
    return live_exit_code(result["valid"], result["quality_status"])


def calibrate(arguments: argparse.Namespace) -> int:
    plan = build_calibration_plan(arguments)
    if arguments.dry_run:
        print_json(plan)
        return 0
    result = run_calibrate(plan)
    print_json(result)
    return calibration_exit_code(result)


def load_reviewable_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    run_path = run_dir / "run.json"
    run = load_json_object(run_path, "run")
    configuration = run.get("configuration")
    if not isinstance(configuration, dict) or configuration.get("evaluation_role") != "promotion":
        raise ValueError("run: must be a promotion run")
    if not run.get("valid") or run.get("quality_status") == "unknown":
        raise ValueError("run: promotion evidence must be runner-valid and quality-complete")
    if configuration.get("trials", 0) < 3:
        raise ValueError("run: promotion evidence must contain at least 3 trials")
    tasks_path = retained_file(run_dir, "tasks.jsonl", "run tasks")
    if hash_file(tasks_path) != configuration.get("tasks_sha256"):
        raise ValueError("run: retained tasks hash does not match the promotion plan")
    return run, configuration, run_path


def prepare_review(arguments: argparse.Namespace) -> int:
    run_dir = absolute_path(arguments.run_dir, "run-dir")
    output = absolute_path(arguments.output, "output")
    run, configuration, run_path = load_reviewable_run(run_dir)
    if output.exists():
        raise ValueError(f"output directory already exists: {output}")

    items: list[ReviewItem] = []
    copied: list[tuple[Path, Path]] = []
    for pair in run.get("pairs", []):
        if not isinstance(pair, dict):
            raise ValueError("run field pairs: must contain objects")
        task_id = required_string(pair.get("task_id"), "run pair field task_id")
        safe_task_id(task_id)
        trial = pair.get("trial")
        if not isinstance(trial, int) or trial < 1:
            raise ValueError("run pair field trial: must be a positive integer")
        report_path = retained_file(run_dir, pair.get("report_json"), "run pair report_json")
        report = load_json_object(report_path, "pair report")
        if not report.get("runner_valid"):
            raise ValueError(f"run pair {task_id} trial {trial}: runner is invalid")
        pairwise = report.get("pairwise")
        if not isinstance(pairwise, list) or not pairwise:
            raise ValueError(f"run pair {task_id} trial {trial}: missing pairwise evidence")
        for index, judgment in enumerate(pairwise, start=1):
            if not isinstance(judgment, dict) or judgment.get("status") == "unknown":
                raise ValueError(
                    f"run pair {task_id} trial {trial} rubric {index}: judgment is incomplete"
                )
            artifacts = judgment.get("artifacts")
            if not isinstance(artifacts, dict):
                raise ValueError(
                    f"run pair {task_id} trial {trial} rubric {index}: missing artifacts"
                )
            prompt_path = retained_file(report_path.parent, artifacts.get("prompt"), "judge prompt")
            dimensions = judgment.get("dimensions")
            if not isinstance(dimensions, list) or not dimensions:
                raise ValueError(
                    f"run pair {task_id} trial {trial} rubric {index}: missing dimensions"
                )
            dimension_names = [
                required_string(dimension.get("name"), "judge dimension field name")
                for dimension in dimensions
                if isinstance(dimension, dict)
            ]
            if len(dimension_names) != len(dimensions):
                raise ValueError("judge dimensions: must contain objects")
            item_id = f"{task_id}.trial-{trial:03d}.rubric-{index:03d}"
            destination = Path("items") / f"{item_id}.txt"
            copied.append((prompt_path, destination))
            items.append(
                ReviewItem(
                    id=item_id,
                    task_id=task_id,
                    trial=trial,
                    rubric_index=index,
                    prompt=destination.as_posix(),
                    prompt_sha256=hash_file(prompt_path),
                    dimensions=tuple(dimension_names),
                    source_report=str(
                        report_path.resolve().relative_to(run_dir.resolve()).as_posix()
                    ),
                )
            )
    if not items:
        raise ValueError("run: no pairwise evidence is available for human review")

    packet = ReviewPacket(
        run_sha256=hash_file(run_path),
        tasks_sha256=configuration["tasks_sha256"],
        items=items,
    )
    packet.write(output, copied)
    print_json(
        {
            "valid": True,
            "mode": "prepare_review",
            "output_dir": str(output),
            "items": len(items),
            "required_reviewers": packet.required_reviewers,
        }
    )
    return 0


def load_reviewer_labels(
    path: Path, manifest_hash: str, items: dict[str, dict[str, Any]]
) -> ReviewerLabels:
    document = load_json_object(path, "labels")
    if document.get("version") != 1:
        raise ValueError("labels field version: must be 1")
    if document.get("manifest_sha256") != manifest_hash:
        raise ValueError("labels: manifest hash does not match the review packet")
    reviewer_id = required_string(document.get("reviewer_id"), "labels field reviewer_id")
    raw_labels = document.get("labels")
    if not isinstance(raw_labels, list) or len(raw_labels) != len(items):
        raise ValueError("labels field labels: must cover every review item exactly once")
    labels: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_labels):
        label = f"labels field labels[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label}: must be an object")
        item_id = required_string(raw.get("item_id"), f"{label} field item_id")
        item = items.get(item_id)
        if item is None or item_id in labels:
            raise ValueError(f"{label} field item_id: unknown or duplicate value")
        if raw.get("prompt_sha256") != item["prompt_sha256"]:
            raise ValueError(f"{label}: prompt hash does not match the review packet")
        winner = raw.get("winner")
        if winner not in {"A", "B", "tie"}:
            raise ValueError(f"{label} field winner: must be A, B, or tie")
        rationale = required_string(raw.get("rationale"), f"{label} field rationale")
        if raw.get("transcript_reviewed") is not True:
            raise ValueError(f"{label} field transcript_reviewed: must be true")
        raw_dimensions = raw.get("dimensions")
        if not isinstance(raw_dimensions, list) or len(raw_dimensions) != len(item["dimensions"]):
            raise ValueError(f"{label} field dimensions: must cover every dimension exactly once")
        dimensions: dict[str, dict[str, str]] = {}
        for dimension_index, raw_dimension in enumerate(raw_dimensions):
            dimension_label = f"{label} field dimensions[{dimension_index}]"
            if not isinstance(raw_dimension, dict):
                raise ValueError(f"{dimension_label}: must be an object")
            name = required_string(raw_dimension.get("name"), f"{dimension_label} field name")
            if name not in item["dimensions"] or name in dimensions:
                raise ValueError(f"{dimension_label} field name: unknown or duplicate value")
            dimension_winner = raw_dimension.get("winner")
            if dimension_winner not in {"A", "B", "tie"}:
                raise ValueError(f"{dimension_label} field winner: must be A, B, or tie")
            dimensions[name] = {
                "winner": dimension_winner,
                "rationale": required_string(
                    raw_dimension.get("rationale"), f"{dimension_label} field rationale"
                ),
            }
        labels[item_id] = {
            "winner": winner,
            "rationale": rationale,
            "transcript_reviewed": True,
            "dimensions": dimensions,
        }
    return ReviewerLabels(reviewer_id=reviewer_id, labels=labels)


def count_agreement(left: str, right: str) -> int:
    return int(left == right)


def finalize_review(arguments: argparse.Namespace) -> int:
    run_dir = absolute_path(arguments.run_dir, "run-dir")
    manifest_path = absolute_path(arguments.manifest, "manifest")
    output = absolute_path(arguments.output, "output")
    if output.exists():
        raise ValueError(f"output directory already exists: {output}")
    if not math.isfinite(arguments.cost_usd) or arguments.cost_usd < 0:
        raise ValueError("cost-usd must be a finite non-negative number")
    cost_note = required_string(arguments.cost_note, "cost-note")
    run, configuration, run_path = load_reviewable_run(run_dir)
    manifest = load_json_object(manifest_path, "manifest")
    if manifest.get("version") != 1 or manifest.get("required_reviewers") != 2:
        raise ValueError("manifest: unsupported review packet")
    if manifest.get("run_sha256") != hash_file(run_path):
        raise ValueError("manifest: run hash does not match the retained promotion run")
    if manifest.get("tasks_sha256") != configuration.get("tasks_sha256"):
        raise ValueError("manifest: tasks hash does not match the retained promotion run")
    attestation_path = absolute_path(arguments.holdout_attestation, "holdout-attestation")
    attestation = load_json_object(attestation_path, "holdout attestation")
    if attestation.get("version") != 1:
        raise ValueError("holdout attestation field version: must be 1")
    if attestation.get("tasks_sha256") != configuration.get("tasks_sha256"):
        raise ValueError("holdout attestation: tasks hash does not match the promotion run")
    custodian_id = required_string(
        attestation.get("custodian_id"), "holdout attestation field custodian_id"
    )
    if attestation.get("independent_of_skill_authoring") is not True:
        raise ValueError("holdout attestation field independent_of_skill_authoring: must be true")
    if attestation.get("unseen_during_development") is not True:
        raise ValueError("holdout attestation field unseen_during_development: must be true")
    coverage = attestation.get("coverage")
    required_coverage = {"positive", "negative", "ambiguous", "near_tie", "adversarial"}
    if not isinstance(coverage, dict) or set(coverage) != required_coverage or not all(
        coverage.values()
    ):
        raise ValueError(
            "holdout attestation field coverage: every required category must be true"
        )
    holdout_rationale = required_string(
        attestation.get("rationale"), "holdout attestation field rationale"
    )
    raw_items = manifest.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("manifest field items: must be a non-empty array")
    items: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_items):
        label = f"manifest field items[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: must be an object")
        item_id = required_string(item.get("id"), f"{label} field id")
        if item_id in items:
            raise ValueError(f"{label} field id: duplicate value")
        prompt = retained_file(manifest_path.parent, item.get("prompt"), f"{label} field prompt")
        if hash_file(prompt) != item.get("prompt_sha256"):
            raise ValueError(f"{label}: prompt hash does not match the manifest")
        dimensions = item.get("dimensions")
        if not isinstance(dimensions, list) or not dimensions:
            raise ValueError(f"{label} field dimensions: must be a non-empty array")
        items[item_id] = item
    if len(arguments.labels) != 2:
        raise ValueError("finalize-review requires exactly two independent label files")
    manifest_hash = hash_file(manifest_path)
    label_paths = [absolute_path(value, "labels") for value in arguments.labels]
    reviews = [
        load_reviewer_labels(path, manifest_hash, items) for path in label_paths
    ]
    reviewers = [review.reviewer_id for review in reviews]
    if len(set(reviewers)) != 2:
        raise ValueError("labels: reviewer_id values must be distinct")

    overall_agreements = 0
    dimension_agreements = 0
    dimension_total = 0
    judge_consensus_agreements = 0
    judge_consensus_total = 0
    judge_dimension_consensus_agreements = 0
    judge_dimension_consensus_total = 0
    judge_by_reviewer = {
        reviewer: {
            "overall": {"agreements": 0, "total": 0},
            "dimensions": {"agreements": 0, "total": 0},
        }
        for reviewer in reviewers
    }
    disagreements: list[dict[str, Any]] = []
    outcomes = {"control": 0, "treatment": 0, "tie": 0}
    by_task: dict[str, dict[str, int]] = {}
    regressions: list[str] = []
    improvements: list[str] = []
    for item_id, item in items.items():
        left = reviews[0].labels[item_id]
        right = reviews[1].labels[item_id]
        agreed = left["winner"] == right["winner"]
        overall_agreements += int(agreed)
        dimension_disagreements: list[str] = []
        for name in item["dimensions"]:
            dimension_total += 1
            dimension_agreements += count_agreement(
                left["dimensions"][name]["winner"], right["dimensions"][name]["winner"]
            )
            if left["dimensions"][name]["winner"] != right["dimensions"][name]["winner"]:
                dimension_disagreements.append(name)
        report_path = retained_file(run_dir, item.get("source_report"), "manifest source_report")
        report = load_json_object(report_path, "pair report")
        pairwise = report.get("pairwise")
        rubric_index = item.get("rubric_index")
        if not isinstance(pairwise, list) or not isinstance(rubric_index, int) or not (
            1 <= rubric_index <= len(pairwise)
        ):
            raise ValueError(f"manifest item {item_id}: pairwise source is invalid")
        automated = pairwise[rubric_index - 1]
        automated_winner = automated.get("winner_label")
        mapping = automated.get("mapping")
        if automated_winner not in {"A", "B", "tie"} or not isinstance(mapping, dict):
            raise ValueError(f"manifest item {item_id}: automated judgment is incomplete")
        automated_dimensions = {
            dimension.get("name"): dimension.get("winner")
            for dimension in automated.get("dimensions", [])
            if isinstance(dimension, dict)
        }
        if set(automated_dimensions) != set(item["dimensions"]):
            raise ValueError(f"manifest item {item_id}: automated dimensions are incomplete")
        for reviewer, human in zip(reviewers, (left, right)):
            judge_by_reviewer[reviewer]["overall"]["total"] += 1
            judge_by_reviewer[reviewer]["overall"]["agreements"] += int(
                automated_winner == human["winner"]
            )
            for name in item["dimensions"]:
                judge_by_reviewer[reviewer]["dimensions"]["total"] += 1
                judge_by_reviewer[reviewer]["dimensions"]["agreements"] += int(
                    automated_dimensions[name] == human["dimensions"][name]["winner"]
                )
        if agreed:
            judge_consensus_total += 1
            judge_consensus_agreements += int(automated_winner == left["winner"])
            outcome = "tie" if left["winner"] == "tie" else mapping.get(left["winner"])
            if outcome not in outcomes:
                raise ValueError(f"manifest item {item_id}: candidate mapping is invalid")
            outcomes[outcome] += 1
            task_outcomes = by_task.setdefault(
                required_string(item.get("task_id"), "manifest item field task_id"),
                {"control": 0, "treatment": 0, "tie": 0},
            )
            task_outcomes[outcome] += 1
            if outcome == "control":
                regressions.append(item_id)
            elif outcome == "treatment":
                improvements.append(item_id)
        for name in item["dimensions"]:
            left_winner = left["dimensions"][name]["winner"]
            right_winner = right["dimensions"][name]["winner"]
            if left_winner == right_winner:
                judge_dimension_consensus_total += 1
                judge_dimension_consensus_agreements += int(
                    automated_dimensions[name] == left_winner
                )
        if not agreed or dimension_disagreements:
            disagreements.append(
                {
                    "item_id": item_id,
                    "overall": {
                        reviewers[0]: {"winner": left["winner"], "rationale": left["rationale"]},
                        reviewers[1]: {
                            "winner": right["winner"],
                            "rationale": right["rationale"],
                        },
                    },
                    "dimensions": dimension_disagreements,
                }
            )

    trial_variance = {
        task_id: {
            "status": "stable" if sum(count > 0 for count in counts.values()) == 1 else "mixed",
            "outcomes": counts,
        }
        for task_id, counts in by_task.items()
    }
    report = {
        "version": 1,
        "evidence_status": "complete_human_review",
        "promotion_decision": "human_owner_required",
        "run_binding": {
            "run_sha256": hash_file(run_path),
            "tasks_sha256": configuration["tasks_sha256"],
            "skill_sha256": configuration["skill_sha256"],
            "calibration_sha256": configuration.get("calibration_sha256"),
            "fixtures_sha256": configuration.get("fixtures_sha256"),
        },
        "reviewers": reviewers,
        "holdout": {
            "custodian_id": custodian_id,
            "independent_of_skill_authoring": True,
            "unseen_during_development": True,
            "coverage": coverage,
            "rationale": holdout_rationale,
        },
        "human_agreement": Agreement(
            overall_agreements=overall_agreements,
            overall_total=len(items),
            dimension_agreements=dimension_agreements,
            dimension_total=dimension_total,
            disagreements=disagreements,
        ).as_dict(),
        "disagreements": disagreements,
        "automated_judge_agreement": {
            "status": "provisional_non_independent",
            "with_human_consensus": {
                "agreements": judge_consensus_agreements,
                "total": judge_consensus_total,
            },
            "dimensions_with_human_consensus": {
                "agreements": judge_dimension_consensus_agreements,
                "total": judge_dimension_consensus_total,
            },
            "by_reviewer": judge_by_reviewer,
        },
        "transcript_review": {
            "reviewed_labels": len(items) * 2,
            "required_labels": len(items) * 2,
        },
        "outcomes": outcomes,
        "outcomes_by_task": by_task,
        "trial_variance": trial_variance,
        "improvements": improvements,
        "regressions": regressions,
        "usage": run.get("usage"),
        "cost": {"usd": arguments.cost_usd, "note": cost_note},
        "artifacts": {
            "manifest": {"path": "manifest.json", "sha256": hash_file(manifest_path)},
            "holdout_attestation": {
                "path": "holdout-attestation.json",
                "sha256": hash_file(attestation_path),
            },
            "reviewer_1": {
                "path": "reviewer-001.json",
                "sha256": hash_file(label_paths[0]),
            },
            "reviewer_2": {
                "path": "reviewer-002.json",
                "sha256": hash_file(label_paths[1]),
            },
        },
        "limitations": [
            "Human identities and holdout custody are operator attestations, not machine-proven.",
            "Automated judging remains same-provider and non-independent.",
            "This evidence package informs but does not make the promotion decision.",
        ],
    }
    output.mkdir(parents=True)
    for source, name in (
        (manifest_path, "manifest.json"),
        (attestation_path, "holdout-attestation.json"),
        (label_paths[0], "reviewer-001.json"),
        (label_paths[1], "reviewer-002.json"),
    ):
        shutil.copyfile(source, output / name)
    write_json(output / "promotion-review.json", report)
    (output / "promotion-review.md").write_text(
        "\n".join(
            [
                "# Promotion review",
                "",
                "Evidence status: complete human review",
                "Promotion decision: human owner required",
                f"Reviewers: {', '.join(reviewers)}",
                f"Human overall agreement: {overall_agreements}/{len(items)}",
                f"Human dimension agreement: {dimension_agreements}/{dimension_total}",
                f"Human disagreements: {len(disagreements)}",
                (
                    "Automated judge agreement with human consensus: "
                    f"{judge_consensus_agreements}/{judge_consensus_total}"
                ),
                f"Outcomes: {json.dumps(outcomes, sort_keys=True)}",
                f"Trial variance: {json.dumps(trial_variance, sort_keys=True)}",
                f"Improvements: {len(improvements)}",
                f"Regressions: {len(regressions)}",
                f"Recorded cost (USD): {arguments.cost_usd:.2f}",
                "",
                "Evidence artifacts:",
                "- [Review manifest](manifest.json)",
                "- [Holdout attestation](holdout-attestation.json)",
                "- [Reviewer 1 labels](reviewer-001.json)",
                "- [Reviewer 2 labels](reviewer-002.json)",
                "",
                "The accountable human owner must inspect disagreements and decide promotion.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print_json(
        {
            "valid": True,
            "mode": "finalize_review",
            "output_dir": str(output),
            "evidence_status": report["evidence_status"],
        }
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="skill-eval-loop")
    commands = result.add_subparsers(dest="command", required=True)
    health = commands.add_parser("healthcheck", help="validate the installed skill")
    health.add_argument("--skill-dir")
    health.set_defaults(handler=healthcheck)
    run_parser = commands.add_parser("run", help="plan or run a paired evaluation")
    run_parser.add_argument("--skill", required=True)
    run_parser.add_argument("--tasks")
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--harness", required=True)
    run_parser.add_argument("--harness-bin")
    run_parser.add_argument("--judge-harness")
    run_parser.add_argument("--judge-harness-bin")
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument("--trials", type=int, default=1)
    run_parser.add_argument("--timeout-seconds", type=int, default=120)
    run_parser.add_argument("--judge-model", default="")
    run_parser.add_argument("--calibration")
    run_parser.add_argument(
        "--promotion",
        action="store_true",
        help="require an explicit task set, accepted rubric calibration, and at least 3 trials",
    )
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(handler=run)
    calibrate_parser = commands.add_parser(
        "calibrate", help="score a locked pairwise judge against human-labeled cases"
    )
    calibrate_parser.add_argument("--fixtures", required=True)
    calibrate_parser.add_argument("--output", required=True)
    calibrate_parser.add_argument("--harness", required=True)
    calibrate_parser.add_argument("--harness-bin")
    calibrate_parser.add_argument("--judge-harness")
    calibrate_parser.add_argument("--judge-harness-bin")
    calibrate_parser.add_argument("--model", required=True)
    calibrate_parser.add_argument("--judge-model", required=True)
    calibrate_parser.add_argument("--timeout-seconds", type=int, default=120)
    calibrate_parser.add_argument("--dry-run", action="store_true")
    calibrate_parser.set_defaults(handler=calibrate)
    review_parser = commands.add_parser(
        "prepare-review", help="create a blinded human-review packet from a promotion run"
    )
    review_parser.add_argument("--run-dir", required=True)
    review_parser.add_argument("--output", required=True)
    review_parser.set_defaults(handler=prepare_review)
    finalize_parser = commands.add_parser(
        "finalize-review", help="measure two human reviews against retained promotion evidence"
    )
    finalize_parser.add_argument("--run-dir", required=True)
    finalize_parser.add_argument("--manifest", required=True)
    finalize_parser.add_argument("--holdout-attestation", required=True)
    finalize_parser.add_argument("--labels", action="append", required=True)
    finalize_parser.add_argument("--cost-usd", type=float, required=True)
    finalize_parser.add_argument("--cost-note", required=True)
    finalize_parser.add_argument("--output", required=True)
    finalize_parser.set_defaults(handler=finalize_review)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.handler(arguments)
    except CalibrationBindingError as exc:
        error(str(exc))
        return 2
    except (OSError, ValueError) as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
