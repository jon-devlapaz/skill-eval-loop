#!/usr/bin/env python3
"""Run a paired Codex skill evaluation with retained deterministic evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePath
import re
import shutil
import subprocess
import sys
import time
from typing import Any


MAX_TASK_BYTES = 4 * 1024 * 1024
SUPPORTED_GRADERS = {"regex", "not_regex", "file_exists", "json_equal", "rubric"}


def error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


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
    else:
        grader["text"] = required_string(raw.get("text"), f"{label} field text")
    return grader


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
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
            if task_id in seen:
                raise ValueError(f'task "{task_id}" field id: duplicate value')
            prompt = required_string(raw.get("prompt"), f'task "{task_id}" field prompt')
            raw_graders = raw.get("graders")
            if not isinstance(raw_graders, list) or not raw_graders:
                raise ValueError(f'task "{task_id}" field graders: must be a non-empty array')
            graders = [
                parse_grader(grader, f'task "{task_id}" field graders[{index}]')
                for index, grader in enumerate(raw_graders)
            ]
            task = dict(raw)
            task.update({"id": task_id, "prompt": prompt, "graders": graders})
            tasks.append(task)
            seen.add(task_id)
    if not tasks:
        raise ValueError("tasks: at least one task is required")
    return tasks


def payload_files(root: Path) -> list[Path]:
    excluded = {"evals", "tests", "__pycache__", ".DS_Store"}
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"symlinked skill payload entry is not allowed: {path}")
        if path.is_file() and path.suffix != ".pyc":
            files.append(path)
    return sorted(files)


def hash_skill(root: Path) -> str:
    digest = hashlib.sha256()
    for path in payload_files(root):
        relative = path.relative_to(root).as_posix().encode()
        mode = b"x" if path.stat().st_mode & 0o111 else b"-"
        digest.update(relative + b"\0" + mode + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_harness(executable: str) -> tuple[str, str]:
    resolved = shutil.which(executable)
    if resolved is None:
        raise ValueError(f"codex executable not found: {executable}")
    try:
        version = subprocess.run(
            [resolved, "--version"], text=True, capture_output=True, check=True
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"read codex version: {exc}") from exc
    if not version:
        raise ValueError("codex returned an empty version")
    return resolved, version


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


def build_plan(arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.harness != "codex":
        raise ValueError("harness must be codex")
    if not arguments.model or arguments.trials < 1 or arguments.timeout_seconds < 1:
        raise ValueError("model, positive trials, and positive timeout-seconds are required")
    skill = absolute_path(arguments.skill, "skill")
    output = absolute_path(arguments.output, "output")
    if not (skill / "SKILL.md").is_file():
        raise ValueError("skill path must contain SKILL.md")
    tasks_path = resolve_tasks_path(skill, arguments.tasks)
    tasks = load_tasks(tasks_path)
    rubrics = sum(
        1 for task in tasks for grader in task["graders"] if grader["type"] == "rubric"
    )
    if rubrics and not arguments.judge_model:
        raise ValueError("judge-model is required when rubric graders are present")
    executable, version = resolve_harness(arguments.harness_bin or "codex")
    paired_trials = len(tasks) * arguments.trials
    target_invocations = paired_trials * 2
    judge_invocations = rubrics * arguments.trials * 2
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
            "harness": "codex",
            "harness_executable": executable,
            "harness_version": version,
            "model": arguments.model,
            "judge_model": arguments.judge_model,
            "trials": arguments.trials,
            "timeout_seconds": arguments.timeout_seconds,
            "output_dir": str(output),
            "execution": "sequential",
            "condition_order": "alternating_control_first",
            "tool_posture": "read_only",
        },
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


def print_json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, indent=2) + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def safe_task_id(task_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_id):
        raise ValueError(f'task "{task_id}" field id: must be path-safe')


def copy_skill_payload(source: Path, destination: Path) -> None:
    for path in payload_files(source):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(path.stat().st_mode & 0o777)


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    home = Path(configured) if configured else Path.home() / ".codex"
    home = home.resolve()
    if not home.is_dir():
        raise ValueError(f"authenticated Codex home is unavailable: {home}")
    return home


def trace_value(event: Any, *keys: str) -> Any:
    current = event
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def parse_trace(path: Path) -> dict[str, Any]:
    observed: dict[str, Any] = {
        "response": "",
        "actual_model": "",
        "session_id": "",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    with path.open(encoding="utf-8") as trace:
        for line in trace:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                observed["actual_model"] = trace_value(event, "model") or ""
            elif event.get("type") == "thread.started":
                observed["session_id"] = trace_value(event, "thread_id") or ""
            elif event.get("type") == "item.completed":
                if trace_value(event, "item", "type") == "agent_message":
                    observed["response"] = str(trace_value(event, "item", "text") or "").strip()
            elif event.get("type") == "turn.completed":
                input_tokens = trace_value(event, "usage", "input_tokens")
                output_tokens = trace_value(event, "usage", "output_tokens")
                if isinstance(input_tokens, int) and input_tokens >= 0:
                    observed["input_tokens"] = input_tokens
                if isinstance(output_tokens, int) and output_tokens >= 0:
                    observed["output_tokens"] = output_tokens
                if observed["input_tokens"] is not None and observed["output_tokens"] is not None:
                    observed["total_tokens"] = observed["input_tokens"] + observed["output_tokens"]
    return observed


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


def run_condition(
    *,
    condition: str,
    pair_dir: Path,
    skill: Path,
    skill_hash: str,
    skill_name: str,
    codex_directory: Path,
    configuration: dict[str, Any],
    task: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, bool]]:
    condition_dir = pair_dir / condition
    workspace = condition_dir / "workspace"
    workspace.mkdir(parents=True)
    (condition_dir / "home").mkdir()
    installed_skill = workspace / ".agents" / "skills" / skill_name
    if installed_skill.exists():
        raise ValueError(f"fixture exposes target skill in {condition}")
    isolation = {"control_skill_absent": condition == "control", "treatment_skill_present": False, "treatment_hash_matches": False}
    if condition == "treatment":
        copy_skill_payload(skill, installed_skill)
        if hash_skill(installed_skill) != skill_hash:
            raise ValueError("installed skill hash does not match source")
        isolation["treatment_skill_present"] = True
        isolation["treatment_hash_matches"] = True
    trace_path = condition_dir / "trace.jsonl"
    stderr_path = condition_dir / "stderr.txt"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(condition_dir / "home"),
            "CODEX_HOME": str(codex_directory),
            "SKILL_EVAL_SKILL_NAME": skill_name,
        }
    )
    arguments = [
        configuration["harness_executable"],
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--model",
        configuration["model"],
        task["prompt"],
    ]
    started = time.monotonic()
    timed_out = False
    try:
        with trace_path.open("w", encoding="utf-8") as trace, stderr_path.open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(
                arguments,
                cwd=workspace,
                env=environment,
                stdout=trace,
                stderr=stderr,
                timeout=configuration["timeout_seconds"],
                check=False,
            )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = -1
    duration_ms = round((time.monotonic() - started) * 1000)
    observed = parse_trace(trace_path)
    response_path = condition_dir / "response.md"
    response_path.write_text(observed["response"], encoding="utf-8")
    deterministic = grade(task, workspace, observed["response"])
    actual_model = observed["actual_model"]
    model_matches = actual_model == configuration["model"] if actual_model else None
    model_requirement_satisfied = bool(configuration["model"]) and (not actual_model or model_matches)
    status = "timed_out" if timed_out else ("completed" if exit_code == 0 else "failed")
    return (
        {
            "name": condition,
            "response": observed["response"],
            "deterministic_status": deterministic["status"],
            "pending_rubrics": deterministic["pending_rubrics"],
            "graders": deterministic["results"],
            "execution": {
                "status": status,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "requested_model": configuration["model"],
                "trace_reported_model": actual_model,
                "model_identity_source": "trace_reported" if actual_model else "cli_configured",
                "model_matches_requested": model_matches,
                "model_requirement_satisfied": model_requirement_satisfied,
                "input_tokens": observed["input_tokens"],
                "output_tokens": observed["output_tokens"],
                "total_tokens": observed["total_tokens"],
            },
            "artifacts": {
                "response": f"{condition}/response.md",
                "trace": f"{condition}/trace.jsonl",
                "stderr": f"{condition}/stderr.txt",
            },
        },
        isolation,
    )


def deterministic_comparison(control: str, treatment: str) -> str:
    if "not_scored" in {control, treatment}:
        return "not_scored"
    return {
        ("pass", "pass"): "both_pass",
        ("fail", "pass"): "treatment_only",
        ("pass", "fail"): "control_only",
        ("fail", "fail"): "both_fail",
    }[(control, treatment)]


def write_pair_report(
    pair_dir: Path,
    task: dict[str, Any],
    trial: int,
    execution_order: list[str],
    skill_name: str,
    skill_hash: str,
    conditions: dict[str, dict[str, Any]],
    isolation: dict[str, bool],
) -> tuple[dict[str, Any], Path, Path]:
    control = conditions["control"]
    treatment = conditions["treatment"]
    runner_valid = (
        control["execution"]["status"] == "completed"
        and treatment["execution"]["status"] == "completed"
        and control["execution"]["model_requirement_satisfied"]
        and treatment["execution"]["model_requirement_satisfied"]
        and isolation["control_skill_absent"]
        and isolation["treatment_skill_present"]
        and isolation["treatment_hash_matches"]
    )
    report = {
        "runner_valid": runner_valid,
        "task": {"id": task["id"], "prompt": task["prompt"], "graders": task["graders"]},
        "trial": trial,
        "execution_order": execution_order,
        "deterministic_comparison": deterministic_comparison(
            control["deterministic_status"], treatment["deterministic_status"]
        ),
        "review_status": "human_transcript_review_required",
        "rubric_status": "pending_human_review" if control["pending_rubrics"] + treatment["pending_rubrics"] else "not_required",
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
    markdown_path.write_text(
        "\n".join(
            [
                f"# {task['id']} trial {trial}",
                "",
                f"Runner valid: {runner_valid}",
                f"Deterministic comparison: {report['deterministic_comparison']}",
                f"Execution order: {', '.join(execution_order)}",
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
    tasks = load_tasks(tasks_path)
    for task in tasks:
        safe_task_id(task["id"])
        if any(grader["type"] == "rubric" for grader in task["graders"]):
            raise ValueError(f'task "{task["id"]}": rubric graders are not supported by live minimum runs yet')
    if hash_file(tasks_path) != configuration["tasks_sha256"]:
        raise ValueError("tasks changed after dry-run planning")
    if hash_skill(skill) != configuration["skill_sha256"]:
        raise ValueError("skill changed after dry-run planning")
    if output.exists():
        raise ValueError(f"output directory already exists: {output}")
    codex_directory = codex_home()
    skill_name = skill.name
    if (codex_directory / "skills" / skill_name).exists():
        raise ValueError(f"target skill already exists in authenticated Codex home: {codex_directory / 'skills' / skill_name}")
    output.mkdir(parents=True)
    write_json(output / "config.json", {"mode": "live", "configuration": configuration, "counts": plan["counts"]})
    shutil.copyfile(tasks_path, output / "tasks.jsonl")
    result: dict[str, Any] = {
        "valid": True,
        "mode": "live",
        "output_dir": str(output),
        "configuration": configuration,
        "counts": plan["counts"],
        "pairs": [],
    }
    for task in tasks:
        for trial in range(1, configuration["trials"] + 1):
            pair_dir = output / f"task-{task['id']}" / f"trial-{trial:03d}"
            pair_dir.mkdir(parents=True)
            execution_order = ["control", "treatment"] if trial % 2 else ["treatment", "control"]
            conditions: dict[str, dict[str, Any]] = {}
            isolation = {"control_skill_absent": False, "treatment_skill_present": False, "treatment_hash_matches": False}
            for condition in execution_order:
                condition_result, current_isolation = run_condition(
                    condition=condition,
                    pair_dir=pair_dir,
                    skill=skill,
                    skill_hash=configuration["skill_sha256"],
                    skill_name=skill_name,
                    codex_directory=codex_directory,
                    configuration=configuration,
                    task=task,
                )
                conditions[condition] = condition_result
                for key, value in current_isolation.items():
                    isolation[key] = isolation[key] or value
            report, report_path, markdown_path = write_pair_report(
                pair_dir,
                task,
                trial,
                execution_order,
                skill_name,
                configuration["skill_sha256"],
                conditions,
                isolation,
            )
            if not report["runner_valid"]:
                result["valid"] = False
            result["pairs"].append(
                {
                    "task_id": task["id"],
                    "trial": trial,
                    "runner_valid": report["runner_valid"],
                    "execution_order": execution_order,
                    "report_json": str(report_path.relative_to(output).as_posix()),
                    "report_markdown": str(markdown_path.relative_to(output).as_posix()),
                }
            )
    write_json(output / "run.json", result)
    return result


def healthcheck(arguments: argparse.Namespace) -> int:
    root = Path(arguments.skill_dir).resolve() if arguments.skill_dir else Path(__file__).resolve().parents[1]
    required = ["SKILL.md", "scripts/skill_eval_loop.py", "scripts/skill-eval-loop"]
    missing = [relative for relative in required if not (root / relative).is_file()]
    print_json(
        {
            "valid": not missing,
            "skill_dir": str(root),
            "commands": ["healthcheck", "run"],
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
    return 0 if result["valid"] else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="skill-eval-loop")
    commands = result.add_subparsers(dest="command", required=True)
    health = commands.add_parser("healthcheck", help="validate the installed skill")
    health.add_argument("--skill-dir")
    health.set_defaults(handler=healthcheck)
    run_parser = commands.add_parser("run", help="plan or run a paired Codex evaluation")
    run_parser.add_argument("--skill", required=True)
    run_parser.add_argument("--tasks")
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--harness", required=True)
    run_parser.add_argument("--harness-bin")
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument("--trials", type=int, default=1)
    run_parser.add_argument("--timeout-seconds", type=int, default=120)
    run_parser.add_argument("--judge-model", default="")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(handler=run)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.handler(arguments)
    except (OSError, ValueError) as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
