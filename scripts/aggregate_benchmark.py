#!/usr/bin/env python3
"""Validate and summarize one paired harness skill-evaluation run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from runtime_adapters import (
    HARNESS_NAMES,
    model_matches,
    skill_payload_sha256,
    trace_metadata,
)


CONDITIONS = ("without_skill", "with_skill")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} must be relative to the run")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the run") from exc
    return resolved


def _require_hash(root: Path, record: dict, stem: str, label: str) -> Path:
    path = _artifact_path(root, record.get(f"{stem}_path"), f"{label}.{stem}_path")
    if not path.is_file():
        raise FileNotFoundError(f"{label}.{stem}_path does not exist: {path}")
    expected = record.get(f"{stem}_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected or "")):
        raise ValueError(f"{label}.{stem}_sha256 is not a sha256")
    if _sha256_file(path) != expected:
        raise ValueError(f"{label}.{stem}_sha256 does not match {path}")
    return path


def _load_grading(path: Path, label: str) -> tuple[dict, bool]:
    grading = json.loads(path.read_text(encoding="utf-8"))
    expectations = grading.get("expectations")
    summary = grading.get("summary")
    if not isinstance(expectations, list) or not expectations:
        raise ValueError(f"{label}.expectations must be a non-empty list")
    if not isinstance(summary, dict):
        raise ValueError(f"{label}.summary must be an object")
    names: set[str] = set()
    passed = 0
    for index, item in enumerate(expectations, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{label}.expectations[{index}] must be an object")
        name = item.get("text")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError(f"{label}.expectations[{index}].text is missing or duplicate")
        names.add(name)
        if type(item.get("passed")) is not bool:
            raise ValueError(f"{label}.expectations[{index}].passed must be boolean")
        passed += int(item["passed"])
    total = len(expectations)
    expected = {
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "pass_rate": passed / total,
    }
    if any(summary.get(key) != value for key, value in expected.items()):
        raise ValueError(f"{label}.summary is inconsistent with expectations")
    return grading, passed == total


def _judge_identity_valid(
    *,
    root: Path,
    judge: dict,
    label: str,
    judge_model: str | None,
    harness: str,
) -> bool:
    judge_trace = _require_hash(root, judge, "trace", label)
    judge_attestation = None
    if judge.get("attestation_trace_path"):
        judge_attestation = _require_hash(
            root,
            judge,
            "attestation_trace",
            label,
        )
    valid = bool(judge_model) and model_matches(
        str(judge_model or ""), judge.get("actual_model")
    )
    if harness != "codex":
        return valid
    if judge_attestation is None:
        return False
    metadata = trace_metadata(
        judge_trace,
        "",
        harness=harness,
        requested_model=str(judge_model or ""),
        attestation_trace_path=judge_attestation,
    )
    attested_model = metadata.get("actual_model")
    return (
        valid
        and metadata.get("model_attested") is True
        and model_matches(str(judge_model or ""), attested_model)
        and model_matches(
            str(judge.get("actual_model", "")),
            attested_model,
        )
    )


def _condition_record(
    *,
    root: Path,
    record: object,
    condition: str,
    pair_label: str,
    expected_case_id: str,
    expected_trial: int,
    requested_model: str,
    judge_model: str | None,
    harness: str,
    skill_name: str,
    skill_sha256: str,
) -> dict:
    label = f"{pair_label}.{condition}"
    if not isinstance(record, dict):
        raise ValueError(f"{label} is missing")
    if record.get("condition") != condition:
        raise ValueError(f"{label}.condition does not match its manifest key")
    if (
        record.get("case_id") != expected_case_id
        or record.get("trial") != expected_trial
    ):
        raise ValueError(f"{label} does not match its enclosing pair")
    trace_path = _require_hash(root, record, "trace", label)
    attestation_trace = None
    if record.get("attestation_trace_path"):
        attestation_trace = _require_hash(
            root,
            record,
            "attestation_trace",
            label,
        )
    _require_hash(root, record, "response", label)
    grading_path = _require_hash(root, record, "grading", label)
    grading, task_success = _load_grading(grading_path, label)

    invalid: list[str] = []
    if record.get("timed_out") is True:
        invalid.append("timeout")
    if record.get("exit_code") != 0:
        invalid.append("runtime_error")
    if not model_matches(requested_model, record.get("actual_model")):
        invalid.append("model_mismatch")
    if record.get("model_attested") is False:
        invalid.append("model_not_attested")
    judge_records = record.get("judge_records") or []
    if not isinstance(judge_records, list):
        raise ValueError(f"{label}.judge_records must be a list")
    for index, judge in enumerate(judge_records, start=1):
        judge_label = f"{label}.judge_records[{index}]"
        if not isinstance(judge, dict):
            raise ValueError(f"{judge_label} must be an object")
        if not _judge_identity_valid(
            root=root,
            judge=judge,
            label=judge_label,
            judge_model=judge_model,
            harness=harness,
        ):
            invalid.append("judge_model_mismatch")

    installed_value = record.get("installed_skill_path")
    available = record.get("available_skills")
    installed = None
    if condition == "without_skill":
        if installed_value or available:
            raise ValueError(f"{label} exposes the target skill in the control")
    else:
        installed = _artifact_path(root, installed_value, f"{label}.installed_skill_path")
        if not (installed / "SKILL.md").is_file():
            raise FileNotFoundError(f"{label} installed payload is missing SKILL.md")
        if available != [skill_name]:
            raise ValueError(f"{label}.available_skills must contain only {skill_name}")
        if skill_payload_sha256(installed) != skill_sha256:
            raise ValueError(f"{label} installed payload differs from evaluated skill")

    metadata = trace_metadata(
        trace_path,
        skill_name,
        installed,
        harness=harness,
        requested_model=requested_model,
        attestation_trace_path=attestation_trace,
    )
    if harness == "codex" and attestation_trace is None:
        invalid.append("attestation_trace_missing")
    attested_model = metadata.get("actual_model")
    if metadata.get("model_attested") is not True:
        invalid.append("model_not_attested")
    if not model_matches(requested_model, attested_model):
        invalid.append("model_mismatch")
    if not model_matches(str(record.get("actual_model", "")), attested_model):
        invalid.append("manifest_model_mismatch")

    return {
        "success": task_success and not {"timeout", "runtime_error"} & set(invalid),
        "grading": grading,
        "expectation_names": [
            item["text"] for item in grading["expectations"]
        ],
        "invalid": invalid,
        "skill_available": available == [skill_name],
        "skill_activation": record.get("skill_activation"),
        "expected_skill_loading": record.get("expected_skill_loading"),
        "skill_injection_attested": (
            metadata.get("skill_injection_attested") is True
        ),
        "skill_explicitly_accessed": (
            metadata.get("skill_explicitly_accessed") is True
        ),
        "duration_seconds": record.get("duration_seconds"),
        "total_tokens": record.get("total_tokens"),
        "cost": record.get("cost"),
        "trace_path": str(trace_path.relative_to(root)),
    }


def aggregate(run_dir: Path) -> dict:
    root = run_dir.resolve()
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("run_manifest.json must use schema_version 1")
    harness = manifest.get("harness")
    if harness not in HARNESS_NAMES:
        raise ValueError(f"manifest.harness must be one of {list(HARNESS_NAMES)}")

    skill_name = manifest.get("target_skill_name")
    requested_model = manifest.get("requested_model")
    judge_model = manifest.get("judge_model")
    skill_sha256 = manifest.get("skill_sha256")
    activation_mode = manifest.get("activation_mode", "forced")
    if not isinstance(skill_name, str) or not skill_name:
        raise ValueError("manifest.target_skill_name is missing")
    if not isinstance(requested_model, str) or not requested_model:
        raise ValueError("manifest.requested_model is missing")
    if not re.fullmatch(r"[0-9a-f]{64}", str(skill_sha256 or "")):
        raise ValueError("manifest.skill_sha256 is not a sha256")
    if activation_mode not in {"forced", "autonomous"}:
        raise ValueError("manifest.activation_mode is invalid")

    suite_path = _artifact_path(root, manifest.get("suite_path"), "manifest.suite_path")
    if not suite_path.is_file():
        raise FileNotFoundError(f"suite snapshot does not exist: {suite_path}")
    if _sha256_file(suite_path) != manifest.get("suite_sha256"):
        raise ValueError("manifest.suite_sha256 does not match suite snapshot")
    suite_snapshot = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = suite_snapshot.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("suite snapshot must contain cases")
    case_ids = [
        case.get("id") if isinstance(case, dict) else None
        for case in cases
    ]
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        raise ValueError("suite snapshot cases must have ids")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("suite snapshot case ids must be unique")
    trials_per_case = manifest.get("trials_per_case")
    if type(trials_per_case) is not int or trials_per_case < 1:
        raise ValueError("manifest.trials_per_case must be a positive integer")
    if manifest.get("case_count") != len(case_ids):
        raise ValueError("manifest.case_count does not match suite snapshot")
    expected_pairs = {
        (case_id, trial)
        for case_id in case_ids
        for trial in range(1, trials_per_case + 1)
    }

    provenance_path = manifest.get("provenance_path")
    if provenance_path:
        provenance = _artifact_path(root, provenance_path, "manifest.provenance_path")
        if _sha256_file(provenance) != manifest.get("provenance_sha256"):
            raise ValueError("manifest.provenance_sha256 does not match snapshot")
        snapshot = json.loads(provenance.read_text(encoding="utf-8"))
        for index, record in enumerate(snapshot.get("cases", []), start=1):
            label = f"provenance.cases[{index}]"
            if not isinstance(record, dict):
                raise ValueError(f"{label} must be an object")
            retained = _artifact_path(
                root,
                record.get("retained_artifact_path"),
                f"{label}.retained_artifact_path",
            )
            if _sha256_file(retained) != record.get("retained_artifact_sha256"):
                raise ValueError(f"{label} retained artifact hash does not match")

    reference_validation = manifest.get("reference_validation") or []
    if not isinstance(reference_validation, list):
        raise ValueError("manifest.reference_validation must be a list")
    for ref_index, reference in enumerate(reference_validation, start=1):
        if not isinstance(reference, dict) or reference.get("valid") is not True:
            raise ValueError(f"manifest.reference_validation[{ref_index}] is invalid")
        for judge_index, judge in enumerate(
            reference.get("judge_records") or [],
            start=1,
        ):
            label = (
                f"manifest.reference_validation[{ref_index}]"
                f".judge_records[{judge_index}]"
            )
            if not isinstance(judge, dict):
                raise ValueError(f"{label} must be an object")
            if not _judge_identity_valid(
                root=root,
                judge=judge,
                label=label,
                judge_model=(
                    judge_model if isinstance(judge_model, str) else None
                ),
                harness=harness,
            ):
                invalid_reason = f"reference/{ref_index}: judge_model_mismatch"
                # Reference judge identity invalidates the run but does not
                # make the retained files unreadable.
                reference.setdefault("_invalid_reasons", []).append(invalid_reason)

    trials = manifest.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("manifest.trials must be a non-empty list")
    if (
        manifest.get("pair_count") != len(expected_pairs)
        or len(trials) != len(expected_pairs)
    ):
        raise ValueError("manifest does not contain the complete case/trial matrix")
    counterbalanced = manifest.get("execution_order") == "counterbalanced_by_trial"

    seen_pairs: set[tuple[str, int]] = set()
    totals = {condition: 0 for condition in CONDITIONS}
    operations = {
        condition: {"errors": 0, "timeouts": 0, "tokens": 0, "cost": 0.0}
        for condition in CONDITIONS
    }
    pair_outcomes = {
        "improved": 0,
        "regressed": 0,
        "tied_pass": 0,
        "tied_fail": 0,
    }
    invalid_reasons: list[str] = []
    mechanism_gaps: list[str] = []
    runtime_attestation_gaps: list[str] = []
    routing = {
        "expected_injections": 0,
        "available": 0,
        "injection_attested": 0,
        "explicit_accesses": 0,
        "control_exposures": 0,
        "decisions_scored": 0,
        "decisions_correct": 0,
        "false_positives": 0,
        "false_negatives": 0,
    }

    for index, pair in enumerate(trials, start=1):
        if not isinstance(pair, dict):
            raise ValueError(f"manifest.trials[{index}] must be an object")
        case_id = pair.get("case_id")
        trial = pair.get("trial")
        if not isinstance(case_id, str) or type(trial) is not int:
            raise ValueError(f"manifest.trials[{index}] needs case_id and integer trial")
        key = (case_id, trial)
        if key in seen_pairs:
            raise ValueError(f"duplicate pair: {case_id}/{trial}")
        seen_pairs.add(key)
        pair_label = f"{case_id}/trial-{trial:03d}"
        conditions = pair.get("conditions")
        if not isinstance(conditions, dict) or set(conditions) != set(CONDITIONS):
            raise ValueError(f"{pair_label} must contain exactly {list(CONDITIONS)}")
        execution_order = pair.get("execution_order")
        if execution_order is not None:
            expected_order = (
                ["without_skill", "with_skill"]
                if trial % 2
                else ["with_skill", "without_skill"]
            )
            if execution_order != expected_order:
                raise ValueError(
                    f"{pair_label}.execution_order is not counterbalanced"
                )

        observed: dict[str, dict] = {}
        for condition in CONDITIONS:
            result = _condition_record(
                root=root,
                record=conditions[condition],
                condition=condition,
                pair_label=pair_label,
                expected_case_id=case_id,
                expected_trial=trial,
                requested_model=requested_model,
                judge_model=judge_model if isinstance(judge_model, str) else None,
                harness=harness,
                skill_name=skill_name,
                skill_sha256=str(skill_sha256),
            )
            observed[condition] = result
            totals[condition] += int(result["success"])
            record = conditions[condition]
            operations[condition]["errors"] += int(record.get("exit_code") != 0)
            operations[condition]["timeouts"] += int(record.get("timed_out") is True)
            if type(result["total_tokens"]) is int:
                operations[condition]["tokens"] += result["total_tokens"]
            if isinstance(result["cost"], (int, float)):
                operations[condition]["cost"] += float(result["cost"])
            invalid_reasons.extend(
                f"{pair_label}/{condition}: {reason}"
                for reason in result["invalid"]
            )

        if (
            observed["without_skill"]["expectation_names"]
            != observed["with_skill"]["expectation_names"]
        ):
            raise ValueError(f"{pair_label} conditions grade different expectations")

        treatment = observed["with_skill"]
        mechanism_observed = (
            treatment["skill_injection_attested"]
            or treatment["skill_explicitly_accessed"]
        )
        routing["expected_injections"] += 1
        routing["available"] += int(treatment["skill_available"])
        routing["injection_attested"] += int(
            treatment["skill_injection_attested"]
        )
        routing["explicit_accesses"] += int(
            treatment["skill_explicitly_accessed"]
        )
        control = observed["without_skill"]
        control_exposed = (
            control["skill_available"]
            or control["skill_activation"] not in {None, "none"}
            or control["skill_injection_attested"]
            or control["skill_explicitly_accessed"]
        )
        routing["control_exposures"] += int(control_exposed)
        if activation_mode == "forced" and not mechanism_observed:
            runtime_attestation_gaps.append(
                f"{pair_label}: skill_injection_not_visible_in_trace"
            )
        if (
            activation_mode == "autonomous"
            and treatment["expected_skill_loading"] == "required"
            and not treatment["skill_explicitly_accessed"]
        ):
            runtime_attestation_gaps.append(
                f"{pair_label}: expected_skill_access_not_visible_in_trace"
            )
        if not treatment["skill_available"]:
            mechanism_gaps.append(f"{pair_label}: treatment_skill_unavailable")
        expected_activation = (
            "available_for_autonomous_selection"
            if activation_mode == "autonomous"
            else "forced_command"
        )
        if treatment["skill_activation"] != expected_activation:
            mechanism_gaps.append(
                f"{pair_label}: "
                + (
                    "treatment_activation_mismatch"
                    if activation_mode == "autonomous"
                    else "treatment_skill_not_forced"
                )
            )
        if control_exposed:
            mechanism_gaps.append(f"{pair_label}: control_skill_exposure")
        if activation_mode == "autonomous":
            loading = treatment["expected_skill_loading"]
            accessed = treatment["skill_explicitly_accessed"]
            if loading in {"required", "forbidden"}:
                routing["decisions_scored"] += 1
                correct = accessed if loading == "required" else not accessed
                routing["decisions_correct"] += int(correct)
                routing["false_negatives"] += int(
                    loading == "required" and not accessed
                )
                routing["false_positives"] += int(
                    loading == "forbidden" and accessed
                )

        without = observed["without_skill"]["success"]
        with_skill = observed["with_skill"]["success"]
        outcome = (
            "improved" if with_skill and not without
            else "regressed" if without and not with_skill
            else "tied_pass" if with_skill
            else "tied_fail"
        )
        pair_outcomes[outcome] += 1

    if seen_pairs != expected_pairs:
        raise ValueError("manifest does not contain the complete case/trial matrix")

    invalid_reasons.extend(
        reason
        for reference in reference_validation
        if isinstance(reference, dict)
        for reason in reference.get("_invalid_reasons", [])
    )

    pair_count = len(trials)
    treatment_rate = totals["with_skill"] / pair_count
    control_rate = totals["without_skill"] / pair_count
    delta = treatment_rate - control_rate
    artifact_valid = not invalid_reasons
    mechanism_valid = artifact_valid and not mechanism_gaps
    routing_accuracy = (
        routing["decisions_correct"] / routing["decisions_scored"]
        if routing["decisions_scored"]
        else None
    )
    if delta > 0:
        outcome_verdict = "improved"
    elif delta < 0:
        outcome_verdict = "regressed"
    else:
        outcome_verdict = "no_difference"
    if not artifact_valid:
        verdict = "invalid"
    elif not mechanism_valid:
        verdict = "mechanism_unconfirmed"
    else:
        verdict = outcome_verdict

    return {
        "schema_version": 2,
        "skill_name": skill_name,
        "verdict": verdict,
        "outcome_verdict": outcome_verdict,
        "valid": artifact_valid,
        "artifact_valid": artifact_valid,
        "mechanism_valid": mechanism_valid,
        "runtime_attestation_complete": not runtime_attestation_gaps,
        "activation_mode": activation_mode,
        "selection_verdict": (
            "passed"
            if routing_accuracy == 1.0
            else "failed"
            if routing_accuracy is not None
            else "not_measured"
        ),
        "invalid_reasons": sorted(set(invalid_reasons)),
        "mechanism_gaps": sorted(set(mechanism_gaps)),
        "runtime_attestation_gaps": sorted(set(runtime_attestation_gaps)),
        "pair_count": pair_count,
        "task_success": {
            "without_skill": {
                "passed": totals["without_skill"],
                "rate": round(control_rate, 3),
            },
            "with_skill": {
                "passed": totals["with_skill"],
                "rate": round(treatment_rate, 3),
            },
            "delta": round(delta, 3),
            "pair_outcomes": pair_outcomes,
        },
        "routing": {
            **routing,
            "accuracy": (
                round(routing_accuracy, 3)
                if routing_accuracy is not None
                else None
            ),
        },
        "operations": operations,
        "limits": [
            "This is a local paired diagnostic, not a distribution or significance claim.",
            (
                f"{harness} skill exposure is configured by the selected adapter; "
                "runtime attestation and tool-profile precision vary by harness."
            ),
            (
                "Condition order is counterbalanced by trial; temporal drift remains possible."
                if counterbalanced
                else "Legacy condition order was not counterbalanced; phase-order service drift remains possible."
            ),
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a paired Pi run and write benchmark.json."
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = aggregate(args.run_dir)
        output = args.output or args.run_dir / "benchmark.json"
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
