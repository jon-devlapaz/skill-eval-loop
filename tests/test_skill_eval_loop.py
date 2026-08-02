from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from aggregate_benchmark import aggregate  # noqa: E402
from audit_suite import audit  # noqa: E402
from eval_runtime import HeadlessEvalRun  # noqa: E402
from eval_spec import canonical_sha256, grade_case  # noqa: E402
from herdr_runtime import HerdrEvalRun, PaneSet  # noqa: E402
from model_grader import _parse_grade  # noqa: E402
from process_control import CapturedKeyboardInterrupt, run_captured  # noqa: E402
from recommend_models import (  # noqa: E402
    ModelOption,
    build_recommendation,
    infer_tier,
    parse_pi_models,
)
from run_skill_eval import condition_order, plan_run, run_suite  # noqa: E402
from runtime_adapters import (  # noqa: E402
    HARNESS_NAMES,
    build_invocation,
    build_judge_invocation,
    skill_payload_sha256,
    trace_metadata,
    validate_pinned_model,
)
from workspace_paths import DEFAULT_EVAL_RUNS_ROOT, default_run_output  # noqa: E402


class _FakeHerdrRun:
    def __init__(self, root: Path) -> None:
        self.observer = "herdr"
        self.workspace_id = "fake-workspace"
        self.workspace_label = f"eval:fixture-skill:{root.name}"
        self.roles: list[str] = []
        self.finishes: list[str] = []

    def run_captured(
        self,
        command,
        *,
        cwd,
        env,
        timeout_seconds,
        pane_role,
        title,
        trace_path,
        stderr_path,
    ):
        self.roles.append(pane_role)
        completed, timed_out = run_captured(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        return completed, timed_out

    def finish(self, *, status, summary, artifact_path):
        self.finishes.append(status)

    def cancel_active(self):
        return None


class _InterruptHerdrRun(_FakeHerdrRun):
    def run_captured(self, *args, **kwargs):
        raise KeyboardInterrupt


class WorkflowContractTests(unittest.TestCase):
    def test_missing_evals_require_fresh_subagent_authoring(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        protocol = (
            SKILL_ROOT / "references" / "eval-authoring.md"
        ).read_text(encoding="utf-8")
        self.assertIn("fresh-context subagent", skill_text)
        self.assertIn("coordinator co-author", skill_text)
        self.assertIn("write only `<target-skill>/evals/**`", protocol)
        self.assertIn("at least three distinct", protocol)
        self.assertIn("Do not supply the parent conversation", protocol)

    def test_model_choice_and_setup_changes_require_confirmation(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        remediation = (
            SKILL_ROOT / "references" / "setup-remediation.md"
        ).read_text(encoding="utf-8")
        self.assertIn("recommend_models.py", skill_text)
        self.assertIn("confirm the exact target model", skill_text)
        self.assertIn("explicit yes", remediation)
        self.assertIn("Never ask the user to paste a secret", remediation)

    def test_interaction_asks_one_question_at_a_time(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Ask exactly one question in each message", skill_text)
        self.assertIn("wait for the answer before asking", skill_text)
        self.assertIn("ask a separate question to confirm", skill_text)
        self.assertIn("Setup remediation interrupts", skill_text)
        self.assertIn("stated fix", skill_text)


class ModelRecommendationTests(unittest.TestCase):
    def test_provider_name_does_not_inflate_model_tier(self) -> None:
        self.assertEqual(infer_tier("provider/ordinary-model"), "balanced")

    def test_marker_substrings_do_not_inflate_model_tier(self) -> None:
        self.assertEqual(infer_tier("provider/gemini-3.1-pro"), "quality")

    def test_pi_inventory_parser_uses_exact_provider_model_ids(self) -> None:
        output = """provider model context max-out thinking images
openai-codex gpt-5.6-luna 272K 128K yes yes
openai-codex gpt-5.6-terra 272K 128K yes yes
openai-codex gpt-5.6-sol 272K 128K yes yes
"""
        self.assertEqual(
            [model.id for model in parse_pi_models(output)],
            [
                "openai-codex/gpt-5.6-luna",
                "openai-codex/gpt-5.6-terra",
                "openai-codex/gpt-5.6-sol",
            ],
        )

    def test_standard_task_recommends_balanced_target_and_quality_judge(self) -> None:
        models = [
            ModelOption("provider/luna", "budget", "fixture"),
            ModelOption("provider/terra", "balanced", "fixture"),
            ModelOption("provider/sol", "quality", "fixture"),
        ]
        report = build_recommendation(
            harness="pi",
            models=models,
            task_profile="standard",
            case_count=4,
            model_rubric_count=2,
        )
        self.assertEqual(report["recommended_target"], "provider/terra")
        self.assertEqual(report["recommended_judge"], "provider/sol")
        self.assertEqual(report["pilot_model_calls"], 14)
        self.assertTrue(report["confirmation_required"])

    def test_portability_recommends_a_cross_tier_matrix(self) -> None:
        models = [
            ModelOption("provider/mini", "budget", "fixture"),
            ModelOption("provider/main", "balanced", "fixture"),
            ModelOption("provider/max", "quality", "fixture"),
        ]
        report = build_recommendation(
            harness="codex",
            models=models,
            task_profile="portability",
            case_count=3,
            model_rubric_count=0,
        )
        self.assertEqual(
            report["recommended_targets"],
            ["provider/mini", "provider/main", "provider/max"],
        )
        self.assertIsNone(report["recommended_judge"])

    def test_missing_tier_is_disclosed_as_a_fallback(self) -> None:
        report = build_recommendation(
            harness="hermes",
            models=[ModelOption("provider/main", "balanced", "fixture")],
            task_profile="complex",
            case_count=1,
            model_rubric_count=0,
        )
        self.assertTrue(report["frontier_fallbacks"]["quality"])
        self.assertEqual(report["recommended_target"], "provider/main")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _grading(passed: bool = True) -> dict:
    return {
        "grader": {"kind": "deterministic_mixed", "schema_version": 2},
        "expectations": [
            {
                "text": "Completes the task",
                "passed": passed,
                "evidence": "fixture",
                "grader": "response_contains",
            }
        ],
        "summary": {
            "passed": int(passed),
            "failed": int(not passed),
            "total": 1,
            "pass_rate": float(passed),
        },
    }


def _make_record(
    root: Path,
    *,
    skill_name: str,
    condition: str,
    trial: int,
    passed: bool,
    model: str,
) -> dict:
    condition_dir = root / "eval-case" / f"trial-{trial:03d}" / condition
    outputs = condition_dir / "outputs"
    outputs.mkdir(parents=True)
    trace = outputs / "trace.jsonl"
    response = outputs / "response.md"
    grading = condition_dir / "grading.json"
    trace.write_text("{}\n", encoding="utf-8")
    response.write_text("done\n" if passed else "not done\n", encoding="utf-8")
    _write_json(grading, _grading(passed))
    installed = ""
    available: list[str] = []
    if condition == "with_skill":
        skill = condition_dir / "installed-skill" / skill_name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: fixture\n---\n",
            encoding="utf-8",
        )
        installed = str(skill.relative_to(root))
        available = [skill_name]
    return {
        "case_id": "case",
        "trial": trial,
        "condition": condition,
        "duration_seconds": 0.1,
        "exit_code": 0,
        "timed_out": False,
        "requested_model": model,
        "actual_model": model,
        "available_skills": available,
        "skill_available": condition == "with_skill",
        "skill_activation": (
            "forced_command" if condition == "with_skill" else "none"
        ),
        "installed_skill_path": installed,
        "skill_injection_attested": condition == "with_skill",
        "skill_explicitly_accessed": False,
        "expected_skill_loading": (
            "required" if condition == "with_skill" else "forbidden"
        ),
        "total_tokens": 10,
        "cost": 0.01,
        "trace_path": str(trace.relative_to(root)),
        "trace_sha256": _sha256(trace),
        "response_path": str(response.relative_to(root)),
        "response_sha256": _sha256(response),
        "grading_path": str(grading.relative_to(root)),
        "grading_sha256": _sha256(grading),
    }


def _make_run(root: Path, outcomes: list[tuple[bool, bool]]) -> None:
    skill_name = "candidate"
    model = "provider/model-1"
    pairs = []
    for trial, (without, with_skill) in enumerate(outcomes, start=1):
        conditions = {
            "without_skill": _make_record(
                root,
                skill_name=skill_name,
                condition="without_skill",
                trial=trial,
                passed=without,
                model=model,
            ),
            "with_skill": _make_record(
                root,
                skill_name=skill_name,
                condition="with_skill",
                trial=trial,
                passed=with_skill,
                model=model,
            ),
        }
        pairs.append({"case_id": "case", "trial": trial, "conditions": conditions})
    installed = (
        root
        / "eval-case"
        / "trial-001"
        / "with_skill"
        / "installed-skill"
        / skill_name
    )
    suite = root / "suite_snapshot.json"
    _write_json(suite, {"schema_version": 2, "skill_name": skill_name})
    _write_json(
        root / "run_manifest.json",
        {
            "schema_version": 1,
            "target_skill_name": skill_name,
            "skill_sha256": skill_payload_sha256(installed),
            "suite_path": "suite_snapshot.json",
            "suite_sha256": _sha256(suite),
            "provenance_path": None,
            "provenance_sha256": None,
            "requested_model": model,
            "harness": "pi",
            "pair_count": len(pairs),
            "trials": pairs,
        },
    )


def _make_schema2_skill(root: Path) -> Path:
    skill = root / "fixture-skill"
    (skill / "evals").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fixture-skill\ndescription: fixture\n---\n",
        encoding="utf-8",
    )
    _write_json(
        skill / "evals" / "evals.json",
        {
            "schema_version": 2,
            "skill_name": "fixture-skill",
            "suite_type": "regression",
            "dataset_origin": "author_derived",
            "tool_profile": "no_tools",
            "evals": [
                {
                    "id": "case",
                    "behavior_class": "positive",
                    "prompt": "Return done.",
                    "expected_skill_loading": "required",
                    "graders": [
                        {
                            "name": "Returns done",
                            "type": "response_contains",
                            "value": "done",
                        }
                    ],
                    "reference": {"response": "done"},
                }
            ],
        },
    )
    return skill


def _make_schema3_skill(root: Path, *, tamper: bool = False) -> Path:
    skill = root / "candidate-skill"
    provenance_dir = skill / "evals" / "provenance"
    provenance_dir.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: candidate-skill\ndescription: fixture\n---\n",
        encoding="utf-8",
    )
    artifact = provenance_dir / "source.json"
    _write_json(artifact, {"source": "test fixture"})
    case = {
        "id": "case",
        "behavior_class": "positive",
        "routing_class": "should_trigger",
        "prompt": "Return done.",
        "expected_skill_loading": "required",
        "graders": [
            {
                "name": "Returns done",
                "type": "response_contains",
                "value": "done",
            }
        ],
        "reference": {"response": "done"},
    }
    suite = {
        "schema_version": 3,
        "skill_name": "candidate-skill",
        "suite_type": "regression",
        "dataset_origin": "author_derived",
        "tool_profile": "no_tools",
        "provenance_manifest": "provenance.json",
        "distribution_policy": {
            "minimum_pairs": 3,
            "minimum_effect_size": 0.1,
            "confidence_level": 0.95,
        },
        "evals": [case],
    }
    _write_json(skill / "evals" / "evals.json", suite)
    _write_json(
        skill / "evals" / "provenance.json",
        {
            "schema_version": 1,
            "suite_sha256": canonical_sha256(suite),
            "cases": [
                {
                    "case_id": "case",
                    "origin": "author_derived",
                    "source_id": "fixture-1",
                    "source_type": "author_scenario",
                    "observed_at": "2026-07-29",
                    "task_author": "test",
                    "artifact": "provenance/source.json",
                    "artifact_sha256": _sha256(artifact),
                    "case_sha256": canonical_sha256(case),
                }
            ],
        },
    )
    if tamper:
        _write_json(artifact, {"source": "changed after registration"})
    return skill


class SuiteAuditTests(unittest.TestCase):
    def test_valid_provenance_suite_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = audit(_make_schema3_skill(Path(temp)))
            self.assertTrue(report["valid"])
            self.assertEqual(report["provenance_case_count"], 1)

    def test_tampered_provenance_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = audit(_make_schema3_skill(Path(temp), tamper=True))
            self.assertFalse(report["valid"])
            self.assertEqual(report["errors"], ["provenance_hash_mismatch"])


class RuntimeTests(unittest.TestCase):
    def test_harness_choices_are_explicit_and_complete(self) -> None:
        self.assertEqual(
            HARNESS_NAMES,
            ("hermes", "claude-code", "codex", "pi"),
        )

    def test_each_harness_isolates_the_skill_to_treatment(self) -> None:
        expected_markers = {
            "hermes": "--skills",
            "claude-code": "/fixture-skill",
            "codex": "$fixture-skill",
            "pi": "--skill",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            for harness, marker in expected_markers.items():
                with self.subTest(harness=harness):
                    treatment = build_invocation(
                        harness=harness,
                        executable=harness,
                        condition="with_skill",
                        condition_dir=root / harness / "with",
                        skill_path=skill,
                        prompt="Return done.",
                        model="provider/model-1",
                        tool_profile="no_tools",
                    )
                    control = build_invocation(
                        harness=harness,
                        executable=harness,
                        condition="without_skill",
                        condition_dir=root / harness / "without",
                        skill_path=skill,
                        prompt="Return done.",
                        model="provider/model-1",
                        tool_profile="no_tools",
                    )
                    self.assertIn(marker, " ".join(treatment.command))
                    self.assertNotIn(marker, " ".join(control.command))
                    self.assertEqual(treatment.available_skills, ["fixture-skill"])
                    self.assertEqual(control.available_skills, [])
                    self.assertIsNotNone(treatment.installed_skill_path)
                    self.assertIsNone(control.installed_skill_path)
                    self.assertEqual(treatment.exposed_tools, control.exposed_tools)

    def test_each_harness_builds_a_skill_free_judge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for harness in HARNESS_NAMES:
                with self.subTest(harness=harness):
                    invocation = build_judge_invocation(
                        harness=harness,
                        executable=harness,
                        model="provider/model-1",
                        prompt="Return JSON.",
                        run_dir=root / harness,
                    )
                    command = " ".join(invocation.command).lower()
                    self.assertEqual(invocation.available_skills, [])
                    self.assertEqual(invocation.exposed_tools, [])
                    self.assertNotIn("fixture-skill", command)

    def test_pi_changes_only_explicit_skill_availability(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            treatment = build_invocation(
                executable="pi",
                condition="with_skill",
                condition_dir=root / "with",
                skill_path=skill,
                prompt="Return done.",
                model="provider/model-1",
                tool_profile="no_tools",
            )
            control = build_invocation(
                executable="pi",
                condition="without_skill",
                condition_dir=root / "without",
                skill_path=skill,
                prompt="Return done.",
                model="provider/model-1",
                tool_profile="no_tools",
            )
            self.assertIn("--no-skills", treatment.command)
            self.assertIn("--skill", treatment.command)
            self.assertNotIn("--skill", control.command)
            self.assertEqual(treatment.exposed_tools, control.exposed_tools)
            self.assertEqual(treatment.available_skills, ["fixture-skill"])
            self.assertEqual(control.available_skills, [])
            self.assertEqual(
                treatment.command[-1],
                "/skill:fixture-skill Return done.",
            )
            self.assertEqual(control.command[-1], "Return done.")
            self.assertFalse((treatment.installed_skill_path / "evals").exists())

    def test_autonomous_mode_leaves_the_treatment_task_unexpanded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            treatment = build_invocation(
                executable="pi",
                condition="with_skill",
                condition_dir=root / "with",
                skill_path=skill,
                prompt="Return done.",
                model="provider/model-1",
                tool_profile="read_only",
                activation_mode="autonomous",
            )
            self.assertEqual(treatment.command[-1], "Return done.")
            self.assertIn("--skill", treatment.command)
            self.assertEqual(
                treatment.skill_activation,
                "available_for_autonomous_selection",
            )
            self.assertEqual(
                treatment.exposed_tools,
                ["read", "grep", "find", "ls"],
            )

    def test_moving_model_aliases_are_rejected(self) -> None:
        for model in ("auto", "default", "provider/latest"):
            with self.assertRaises(ValueError):
                validate_pinned_model(model)

    def test_trace_separates_injection_from_explicit_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trace = Path(temp) / "trace.jsonl"
            trace.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "system",
                                "subtype": "init",
                                "skills": ["fixture-skill"],
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "done"}],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            metadata = trace_metadata(trace, "fixture-skill")
            self.assertTrue(metadata["skill_injection_attested"])
            self.assertFalse(metadata["skill_explicitly_accessed"])

    def test_trace_does_not_infer_actual_model_from_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trace = Path(temp) / "trace.txt"
            trace.write_text("plain harness response\n", encoding="utf-8")
            metadata = trace_metadata(
                trace,
                "",
                harness="hermes",
                requested_model="provider/requested-model",
            )
            self.assertEqual(metadata["actual_model"], "")
            self.assertFalse(metadata["model_attested"])

    def test_hermes_no_tools_uses_an_explicit_disabled_toolset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            condition_dir = root / "condition"
            invocation = build_invocation(
                harness="hermes",
                executable="hermes",
                condition="with_skill",
                condition_dir=condition_dir,
                skill_path=skill,
                prompt="task",
                model="provider/model-1",
                tool_profile="no_tools",
            )
            config = json.loads(
                (condition_dir / "hermes-config.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(config["agent"]["disabled_toolsets"], ["file"])
            self.assertEqual(invocation.tool_enforcement, "disabled_toolset")
            self.assertNotIn("--source", invocation.command)

    def test_codex_uses_an_isolated_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            condition_dir = root / "condition"
            invocation = build_invocation(
                harness="codex",
                executable="codex",
                condition="without_skill",
                condition_dir=condition_dir,
                skill_path=skill,
                prompt="task",
                model="gpt-5.6-terra",
                tool_profile="no_tools",
            )
            self.assertEqual(
                invocation.env["CODEX_HOME"],
                str(condition_dir / "codex-home"),
            )
            self.assertEqual(invocation.tool_enforcement, "sandbox_posture_only")


class ProcessControlTests(unittest.TestCase):
    def test_timeout_terminates_process_group(self) -> None:
        started = time.monotonic()
        completed, timed_out = run_captured(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess,sys,time;"
                    "subprocess.Popen([sys.executable,'-c','import time;"
                    "time.sleep(30)']);"
                    "time.sleep(30)"
                ),
            ],
            env=os.environ.copy(),
            timeout_seconds=0.1,
            termination_grace_seconds=0.1,
        )
        self.assertTrue(timed_out)
        self.assertEqual(completed.returncode, 124)
        self.assertLess(time.monotonic() - started, 2)

    def test_keyboard_interrupt_terminates_headless_process_group(self) -> None:
        process = MagicMock()
        process.pid = 12345
        process.communicate.side_effect = [KeyboardInterrupt, ("", "")]
        with (
            patch("process_control.subprocess.Popen", return_value=process),
            patch("process_control.os.killpg") as killpg,
        ):
            with self.assertRaises(CapturedKeyboardInterrupt) as raised:
                run_captured(["fixture"], timeout_seconds=1)
        killpg.assert_called_once_with(12345, signal.SIGTERM)
        self.assertEqual(raised.exception.completed.returncode, 130)

    def test_headless_runtime_preserves_partial_interrupt_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["fixture"],
            returncode=130,
            stdout='{"type":"partial"}\n',
            stderr="Interrupted by user.\n",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trace = root / "trace.jsonl"
            stderr = root / "stderr.txt"
            with patch(
                "eval_runtime.run_captured",
                side_effect=CapturedKeyboardInterrupt(completed),
            ):
                with self.assertRaises(CapturedKeyboardInterrupt):
                    HeadlessEvalRun().run_captured(
                        ["fixture"],
                        cwd=root,
                        env=os.environ.copy(),
                        timeout_seconds=1,
                        pane_role="control",
                        title="fixture",
                        trace_path=trace,
                        stderr_path=stderr,
                    )
            self.assertEqual(trace.read_text(encoding="utf-8"), completed.stdout)
            self.assertEqual(stderr.read_text(encoding="utf-8"), completed.stderr)


class PlanningTests(unittest.TestCase):
    @patch("run_skill_eval.resolve_harness", return_value=("/usr/local/bin/pi", "1.0"))
    def test_default_dry_run_path_is_external_and_not_created(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temp:
            skill = _make_schema2_skill(Path(temp))
            output = default_run_output(skill.name)
            with patch("herdr_runtime.HerdrEvalRun._cli") as cli:
                report = plan_run(
                    skill_path=skill,
                    output_dir=output,
                    model="provider/model-1",
                    trials=3,
                )
            cli.assert_not_called()
            self.assertTrue(Path(report["output_dir"]).is_relative_to(DEFAULT_EVAL_RUNS_ROOT))
            self.assertFalse(output.exists())
            self.assertEqual(
                report["model_calls"],
                {"base": 6, "judge": 0, "total": 6},
            )
            self.assertEqual(report["observer"]["kind"], "headless")
            self.assertEqual(
                report["execution_order"]["policy"],
                "counterbalanced_by_trial",
            )

    @patch("run_skill_eval.resolve_harness", return_value=("/usr/local/bin/pi", "1.0"))
    def test_external_output_override_is_preserved(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            output = root / "custom-run"
            report = plan_run(
                skill_path=skill,
                output_dir=output,
                model="provider/model-1",
                trials=1,
            )
            self.assertEqual(Path(report["output_dir"]), output.resolve())
            self.assertFalse(output.exists())

    @patch("run_skill_eval.resolve_harness", return_value=("/usr/local/bin/pi", "1.0"))
    def test_output_inside_active_skills_is_rejected(self, _resolve) -> None:
        with tempfile.TemporaryDirectory() as temp:
            skill = _make_schema2_skill(Path(temp))
            with self.assertRaisesRegex(ValueError, "cannot live inside"):
                plan_run(
                    skill_path=skill,
                    output_dir=SKILL_ROOT.parent / "generated-run",
                    model="provider/model-1",
                    trials=1,
                )

    @patch("run_skill_eval._run_condition")
    @patch(
        "run_skill_eval._validate_references",
        side_effect=ValueError("reference solution failed"),
    )
    @patch("run_skill_eval.start_eval_run")
    @patch("run_skill_eval.require_observer_environment")
    @patch("run_skill_eval.resolve_harness", return_value=("/usr/local/bin/pi", "1.0"))
    def test_reference_failure_prevents_trials(
        self,
        _resolve,
        _require,
        start,
        _references,
        run_condition,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            fake = _FakeHerdrRun(root / "run")
            start.return_value = fake
            with self.assertRaisesRegex(ValueError, "reference solution failed"):
                run_suite(
                    skill_path=skill,
                    output_dir=root / "run",
                    model="provider/model-1",
                    trials=1,
                    observer="herdr",
                )
            run_condition.assert_not_called()
            self.assertEqual(fake.finishes, ["failed"])

    @patch("run_skill_eval.resolve_harness", return_value=("/usr/local/bin/pi", "1.0"))
    def test_herdr_observer_requires_environment_before_creating_output(
        self,
        _resolve,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            output = root / "run"
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "HERDR_ENV=1"):
                    run_suite(
                        skill_path=skill,
                        output_dir=output,
                        model="provider/model-1",
                        trials=1,
                        observer="herdr",
                    )
            self.assertFalse(output.exists())

    @patch("herdr_runtime.HerdrEvalRun._cli")
    @patch("herdr_runtime.HerdrEvalRun.require_environment")
    def test_herdr_workspace_uses_named_retained_2x2_layout(
        self,
        _require,
        cli,
    ) -> None:
        cli.side_effect = [
            {
                "workspace": {"workspace_id": "w1"},
                "root_pane": {"pane_id": "w1:p1"},
            },
            {"pane": {"pane_id": "w1:p2"}},
            {"pane": {"pane_id": "w1:p3"}},
            {"pane": {"pane_id": "w1:p4"}},
            {},
            {},
            {},
            {},
            {},
            {},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = HerdrEvalRun.start(
                skill_name="fixture-skill",
                output_dir=root / "run-1",
                cwd=root,
            )
            self.assertEqual(run.workspace_id, "w1")
            self.assertEqual(
                run.panes,
                PaneSet(
                    coordinator="w1:p1",
                    control="w1:p3",
                    with_skill="w1:p2",
                    judge_results="w1:p4",
                ),
            )
            calls = [item.args for item in cli.call_args_list]
            self.assertIn(
                (
                    "workspace",
                    "create",
                    "--cwd",
                    str(root),
                    "--label",
                    "eval:fixture-skill:run-1",
                    "--no-focus",
                ),
                calls,
            )
            self.assertIn(
                (
                    "pane",
                    "split",
                    "w1:p2",
                    "--direction",
                    "right",
                    "--ratio",
                    "0.5",
                    "--cwd",
                    str(root),
                    "--no-focus",
                ),
                calls,
            )
            self.assertEqual(calls[-1], ("workspace", "focus", "w1"))

    @patch("herdr_runtime.HerdrEvalRun._cli", return_value={})
    def test_cancel_targets_only_the_active_eval_pane(self, cli) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = HerdrEvalRun(
                workspace_id="w1",
                workspace_label="eval:test:run",
                panes=PaneSet("w1:p1", "w1:p2", "w1:p3", "w1:p4"),
                output_dir=root,
            )
            run.status_path.parent.mkdir(parents=True)
            run.status_path.write_text("", encoding="utf-8")
            run._active_pane = "w1:p2"
            run.cancel_active()
            cli.assert_any_call("pane", "send-keys", "w1:p2", "ctrl+c")

    @patch("herdr_runtime.HerdrEvalRun._cli", return_value={})
    def test_finish_retains_workspace_and_notifies_once(self, cli) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = HerdrEvalRun(
                workspace_id="w1",
                workspace_label="eval:test:run",
                panes=PaneSet("w1:p1", "w1:p2", "w1:p3", "w1:p4"),
                output_dir=root,
            )
            run.status_path.parent.mkdir(parents=True)
            run.status_path.write_text("", encoding="utf-8")
            run.finish(
                status="completed",
                summary="Verdict: improved",
                artifact_path=root,
            )
            calls = [item.args for item in cli.call_args_list]
            self.assertIn(
                ("workspace", "rename", "w1", "[completed] eval:test:run"),
                calls,
            )
            notifications = [
                call for call in calls if call[:2] == ("notification", "show")
            ]
            self.assertEqual(len(notifications), 1)
            self.assertFalse(
                any(call[:2] == ("workspace", "close") for call in calls)
            )

    def test_cancellation_marks_partial_run_invalid_and_retains_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            output = root / "run"
            fake = _InterruptHerdrRun(output)
            with (
                patch("run_skill_eval.require_observer_environment"),
                patch("run_skill_eval.start_eval_run", return_value=fake),
                patch(
                    "run_skill_eval.resolve_harness",
                    return_value=("/usr/local/bin/pi", "1.0"),
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_suite(
                        skill_path=skill,
                        output_dir=output,
                        model="provider/model-1",
                        trials=1,
                        observer="herdr",
                    )
            state = json.loads(
                (output / "run_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "cancelled")
            self.assertFalse(state["valid"])
            self.assertEqual(state["completed_conditions"], 0)
            self.assertEqual(fake.finishes, ["cancelled"])
            self.assertTrue(output.is_dir())


class EndToEndTests(unittest.TestCase):
    def test_fake_runs_complete_for_every_selected_harness(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            suite_path = skill / "evals" / "evals.json"
            suite = json.loads(suite_path.read_text(encoding="utf-8"))
            suite["evals"][0]["graders"][0]["value"] = "PASS"
            suite["evals"][0]["reference"]["response"] = "PASS"
            _write_json(suite_path, suite)
            fake = root / "fake-harness"
            fake.write_text(
                """#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("fake-harness 1.0")
    raise SystemExit(0)

joined = " ".join(sys.argv)
treatment = (
    "--skill " in joined
    or "--skills " in joined
    or "/fixture-skill" in joined
    or "$fixture-skill" in joined
)
print(json.dumps({
    "type": "system",
    "subtype": "init",
    "model": "provider/model-1",
    "session_id": "fixture-session",
    "skills": ["fixture-skill"] if treatment else [],
}))
print(json.dumps({
    "message": {
        "role": "assistant",
        "model": "provider/model-1",
        "content": [{"type": "text", "text": "PASS" if treatment else "FAIL"}],
        "usage": {"input": 1, "output": 1, "totalTokens": 2},
    }
}))
""",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            for harness in HARNESS_NAMES:
                with self.subTest(harness=harness):
                    report = run_suite(
                        skill_path=skill,
                        output_dir=root / f"run-{harness}",
                        model="provider/model-1",
                        trials=1,
                        harness=harness,
                        harness_bin=str(fake),
                    )
                    self.assertEqual(report["verdict"], "improved")

    def test_fake_pi_run_writes_a_valid_paired_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            suite_path = skill / "evals" / "evals.json"
            suite = json.loads(suite_path.read_text(encoding="utf-8"))
            suite["evals"][0]["graders"][0]["value"] = "PASS"
            suite["evals"][0]["reference"]["response"] = "PASS"
            _write_json(suite_path, suite)
            fake_pi = root / "fake-pi"
            fake_pi.write_text(
                """#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("fake-pi 1.0")
    raise SystemExit(0)

treatment = "--skill" in sys.argv
print(json.dumps({
    "type": "system",
    "subtype": "init",
    "model": "provider/model-1",
    "session_id": "fixture-session",
    "skills": ["fixture-skill"] if treatment else [],
}))
print(json.dumps({
    "message": {
        "role": "assistant",
        "model": "provider/model-1",
        "content": [{"type": "text", "text": "PASS" if treatment else "FAIL"}],
        "usage": {"input": 1, "output": 1, "totalTokens": 2},
    }
}))
""",
                encoding="utf-8",
            )
            fake_pi.chmod(0o755)
            output = root / "run"
            report = run_suite(
                skill_path=skill,
                output_dir=output,
                model="provider/model-1",
                trials=2,
                pi_bin=str(fake_pi),
            )
            self.assertTrue(report["valid"])
            self.assertEqual(report["verdict"], "improved")
            self.assertTrue(report["mechanism_valid"])
            self.assertTrue((output / "run_manifest.json").is_file())
            self.assertTrue((output / "benchmark.json").is_file())
            state = json.loads((output / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["observer"], "headless")
            manifest = json.loads(
                (output / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["execution_schedule"],
                [
                    {
                        "case_id": "case",
                        "trial": 1,
                        "conditions": ["without_skill", "with_skill"],
                    },
                    {
                        "case_id": "case",
                        "trial": 2,
                        "conditions": ["with_skill", "without_skill"],
                    },
                ],
            )

    def test_model_judges_share_the_visible_judge_results_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = _make_schema2_skill(root)
            suite_path = skill / "evals" / "evals.json"
            suite = json.loads(suite_path.read_text(encoding="utf-8"))
            suite["evals"][0]["graders"] = [
                {
                    "name": "Judge completion",
                    "type": "model_rubric",
                    "rubric": "The response completes the task.",
                }
            ]
            _write_json(suite_path, suite)
            fake_pi = root / "fake-pi"
            fake_pi.write_text(
                """#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("fake-pi 1.0")
    raise SystemExit(0)

prompt = sys.argv[-1]
treatment = "--skill" in sys.argv
if prompt.startswith("You are grading one agent response."):
    response = '{"passed": true, "reason": "fixture passes"}'
else:
    response = "done"
print(json.dumps({
    "type": "system",
    "subtype": "init",
    "model": "provider/model-1",
    "session_id": "fixture-session",
    "skills": ["fixture-skill"] if treatment else [],
}))
print(json.dumps({
    "message": {
        "role": "assistant",
        "model": "provider/model-1",
        "content": [{"type": "text", "text": response}],
        "usage": {"input": 1, "output": 1, "totalTokens": 2},
    }
}))
""",
                encoding="utf-8",
            )
            fake_pi.chmod(0o755)
            output = root / "run"
            fake_herdr = _FakeHerdrRun(output)
            with (
                patch("run_skill_eval.require_observer_environment"),
                patch(
                    "run_skill_eval.start_eval_run",
                    return_value=fake_herdr,
                ),
            ):
                report = run_suite(
                    skill_path=skill,
                    output_dir=output,
                    model="provider/model-1",
                    trials=1,
                    pi_bin=str(fake_pi),
                    judge_model="provider/model-1",
                    observer="herdr",
                )
            self.assertTrue(report["valid"])
            self.assertEqual(
                fake_herdr.roles,
                [
                    "judge_results",
                    "control",
                    "judge_results",
                    "with_skill",
                    "judge_results",
                ],
            )

    def test_condition_order_is_counterbalanced_in_the_manifest(self) -> None:
        self.assertEqual(
            condition_order(1),
            ("without_skill", "with_skill"),
        )
        self.assertEqual(
            condition_order(2),
            ("with_skill", "without_skill"),
        )


class AggregateTests(unittest.TestCase):
    def test_paired_outcomes_produce_descriptive_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True), (True, True), (False, False)])
            report = aggregate(root)
            self.assertTrue(report["valid"])
            self.assertEqual(report["verdict"], "improved")
            self.assertEqual(report["task_success"]["delta"], 0.333)
            self.assertEqual(report["task_success"]["pair_outcomes"]["improved"], 1)

    def test_missing_runtime_attestation_is_separate_from_treatment_validity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True)])
            manifest_path = root / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            treatment = manifest["trials"][0]["conditions"]["with_skill"]
            treatment["skill_injection_attested"] = False
            treatment["skill_explicitly_accessed"] = False
            _write_json(manifest_path, manifest)
            report = aggregate(root)
            self.assertTrue(report["artifact_valid"])
            self.assertTrue(report["mechanism_valid"])
            self.assertFalse(report["runtime_attestation_complete"])
            self.assertEqual(report["outcome_verdict"], "improved")
            self.assertEqual(report["verdict"], "improved")

    def test_trace_visible_control_use_blocks_mechanism_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True)])
            manifest_path = root / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            control = manifest["trials"][0]["conditions"]["without_skill"]
            control["skill_injection_attested"] = True
            _write_json(manifest_path, manifest)
            report = aggregate(root)
            self.assertTrue(report["artifact_valid"])
            self.assertFalse(report["mechanism_valid"])
            self.assertEqual(report["outcome_verdict"], "improved")
            self.assertEqual(report["verdict"], "mechanism_unconfirmed")

    def test_unforced_treatment_blocks_mechanism_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True)])
            manifest_path = root / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            treatment = manifest["trials"][0]["conditions"]["with_skill"]
            treatment["skill_activation"] = "available_only"
            _write_json(manifest_path, manifest)
            report = aggregate(root)
            self.assertTrue(report["artifact_valid"])
            self.assertFalse(report["mechanism_valid"])
            self.assertEqual(
                report["mechanism_gaps"],
                ["case/trial-001: treatment_skill_not_forced"],
            )
            self.assertEqual(report["verdict"], "mechanism_unconfirmed")

    def test_autonomous_access_is_scored_as_a_routing_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True)])
            manifest_path = root / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["activation_mode"] = "autonomous"
            treatment = manifest["trials"][0]["conditions"]["with_skill"]
            treatment["skill_activation"] = "available_for_autonomous_selection"
            treatment["skill_explicitly_accessed"] = True
            _write_json(manifest_path, manifest)
            report = aggregate(root)
            self.assertTrue(report["mechanism_valid"])
            self.assertEqual(report["selection_verdict"], "passed")
            self.assertEqual(report["routing"]["accuracy"], 1.0)
            self.assertEqual(report["routing"]["decisions_correct"], 1)

    def test_artifact_hash_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True)])
            trace = (
                root
                / "eval-case"
                / "trial-001"
                / "with_skill"
                / "outputs"
                / "trace.jsonl"
            )
            trace.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                aggregate(root)

    def test_inconsistent_grading_summary_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True)])
            path = root / "eval-case" / "trial-001" / "with_skill" / "grading.json"
            grading = json.loads(path.read_text(encoding="utf-8"))
            grading["summary"]["passed"] = 0
            _write_json(path, grading)
            manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
            record = manifest["trials"][0]["conditions"]["with_skill"]
            record["grading_sha256"] = _sha256(path)
            _write_json(root / "run_manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "inconsistent"):
                aggregate(root)

    def test_control_exposure_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _make_run(root, [(False, True)])
            manifest_path = root / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["trials"][0]["conditions"]["without_skill"]["available_skills"] = [
                "candidate"
            ]
            _write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "control"):
                aggregate(root)


class EvaluatorMutationTests(unittest.TestCase):
    def test_deliberate_response_mutations_fail_deterministic_graders(self) -> None:
        graders = [
            {
                "name": "Preserves required fact",
                "type": "response_contains",
                "value": "threshold=28",
            },
            {
                "name": "Includes acceptance evidence",
                "type": "response_contains",
                "value": "CHECK: PASS",
            },
            {
                "name": "Stays within scope",
                "type": "response_not_contains",
                "value": "edited-unrelated-file",
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            reference = "threshold=28\nCHECK: PASS\n"
            self.assertEqual(
                grade_case(
                    workspace=workspace,
                    response=reference,
                    graders=graders,
                )["summary"]["failed"],
                0,
            )
            mutations = [
                "threshold=29\nCHECK: PASS\n",
                "threshold=28\n",
                "threshold=28\nCHECK: PASS\nedited-unrelated-file\n",
            ]
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    self.assertGreater(
                        grade_case(
                            workspace=workspace,
                            response=mutation,
                            graders=graders,
                        )["summary"]["failed"],
                        0,
                    )


class ModelGraderTests(unittest.TestCase):
    def test_json_fence_is_accepted(self) -> None:
        self.assertEqual(
            _parse_grade(
                '```json\n{"passed": true, "reason": "All criteria met."}\n```'
            ),
            {"passed": True, "reason": "All criteria met."},
        )


if __name__ == "__main__":
    unittest.main()
