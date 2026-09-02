"""Calibration fixtures, binding, and drift checks."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from helpers import (
    CALIBRATION_FIXTURES,
    EVALUATOR,
    FAKE_CODEX,
    EvaluatorTestCase,
)


class CalibrationTests(EvaluatorTestCase):
    def test_calibrate_discards_auth_when_config_initialization_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_home = root / "user-home"
            host_auth = user_home / ".codex" / "auth.json"
            host_auth.parent.mkdir(parents=True)
            host_auth.write_text('{"OPENAI_API_KEY":"secret"}\n', encoding="utf-8")
            output = root / "calibration-run"
            spec = importlib.util.spec_from_file_location(
                "skill_eval_loop_calibration_auth_cleanup", EVALUATOR
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            evaluator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(evaluator)
            arguments = evaluator.parser().parse_args(
                [
                    "calibrate",
                    "--fixtures",
                    str(CALIBRATION_FIXTURES),
                    "--output",
                    str(output),
                    "--harness",
                    "codex",
                    "--harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "gpt-5.6-terra",
                    "--judge-model",
                    "gpt-5.6-sol",
                ]
            )
            plan = evaluator.build_calibration_plan(arguments)

            with patch.dict(os.environ, {"HOME": str(user_home)}):
                with patch.object(
                    evaluator, "write_json", side_effect=OSError("config write failed")
                ):
                    with self.assertRaisesRegex(OSError, "config write failed"):
                        evaluator.run_calibrate(plan)

            self.assertFalse((output / "codex-home").exists())


    def test_rubric_run_without_calibration_stays_quality_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, report_path = self.run_live_rubric(
                Path(temporary), use_calibration=False
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["calibration_status"], "not_run")
            self.assertIsNone(report["fixtures_sha256"])
            self.assertEqual(report["quality_status"], "unknown")
            self.assertEqual(report["quality_outcome"], "unknown")


    def test_calibration_mapping_flips_candidate_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, output = self.run_calibrate(
                Path(temporary), extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"}
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            retained = json.loads((output / "calibration.json").read_text(encoding="utf-8"))
            orientations = {case["mapping"]["A"] for case in retained["cases"]}
            self.assertEqual(orientations, {"better", "other"})
            self.assertTrue(any(case["mapping"]["A"] == "better" for case in retained["cases"]))
            self.assertTrue(any(case["mapping"]["B"] == "better" for case in retained["cases"]))


    def test_accepted_calibration_binds_fixture_hash_into_run_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calibration_result, calibration_output = self.run_calibrate(
                root, extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"}
            )
            self.assertEqual(calibration_result.returncode, 0, calibration_result.stderr)
            result, output, report_path = self.run_live_rubric(
                root,
                extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"},
                calibration=calibration_output / "calibration.json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            run_report = json.loads((output / "run.json").read_text(encoding="utf-8"))
            pair_report = json.loads(report_path.read_text(encoding="utf-8"))
            expected_hash = json.loads(
                (calibration_output / "calibration.json").read_text(encoding="utf-8")
            )["configuration"]["fixtures_sha256"]
            for report in (run_report, pair_report):
                self.assertEqual(report["calibration_status"], "accepted")
                self.assertEqual(report["fixtures_sha256"], expected_hash)


    def test_supplied_calibration_invalid_categories_exit_two(self) -> None:
        def make_degenerate(calibration: dict[str, object]) -> None:
            cases = calibration["cases"]
            assert isinstance(cases, list)
            for case in cases:
                assert isinstance(case, dict)
                case["mapping"] = {"A": "better", "B": "other"}
                case["winner_label"] = "tie" if case["human_winner"] == "tie" else "A"
                case["judge_winner"] = "tie" if case["human_winner"] == "tie" else "better"
                case["agrees"] = case["judge_winner"] == case["human_winner"]
            calibration["agreements"] = sum(case["agrees"] for case in cases)
            calibration["accepted"] = calibration["agreements"] >= calibration["minimum_agreements"]

        mutations = {
            "malformed": lambda calibration: None,
            "unaccepted": lambda calibration: calibration.update({"accepted": False}),
            "invalid": lambda calibration: calibration.update({"valid": False}),
            "model_mismatch": lambda calibration: None,
            "judge_model_mismatch": lambda calibration: None,
            "degenerate": make_degenerate,
            "missing_fixture": lambda calibration: calibration["configuration"].update(
                {"fixtures_path": "/missing/calibration-fixtures.json"}
            ),
            "hash_mismatch": lambda calibration: calibration["configuration"].update(
                {"fixtures_sha256": "0" * 64}
            ),
            "extra_non_object_case": lambda calibration: calibration["cases"].append("junk"),
            "missing_case_id": lambda calibration: calibration["cases"][0].pop("id"),
            "unhashable_mapping": lambda calibration: calibration["cases"][0][
                "mapping"
            ].update({"A": []}),
            "unhashable_winner_label": lambda calibration: calibration["cases"][0].update(
                {"winner_label": []}
            ),
            "forged_agreements": lambda calibration: (
                calibration.update({"agreements": 0}),
                [case.update({"agrees": False}) for case in calibration["cases"]],
            ),
        }
        for category, mutate in mutations.items():
            with self.subTest(category=category), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                calibration_result, calibration_output = self.run_calibrate(
                    root, extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"}
                )
                self.assertEqual(calibration_result.returncode, 0, calibration_result.stderr)
                calibration_path = calibration_output / "calibration.json"
                if category == "malformed":
                    calibration_path.write_text("not-json\n", encoding="utf-8")
                else:
                    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
                    mutate(calibration)
                    calibration_path.write_text(json.dumps(calibration), encoding="utf-8")
                runner_model = "different-runner" if category == "model_mismatch" else "gpt-5.6-terra"
                judge_model = "different-judge" if category == "judge_model_mismatch" else "gpt-5.6-sol"
                result, _, _ = self.run_live_rubric(
                    root,
                    runner_model=runner_model,
                    judge_model=judge_model,
                    calibration=calibration_path,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("calibration", result.stderr.lower())
                if category == "degenerate":
                    self.assertIn("both A=better and B=better mappings", result.stderr)


    def test_relative_calibration_path_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, _ = self.run_live_rubric(
                Path(temporary), calibration=Path("relative-calibration.json")
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("calibration path does not exist", result.stderr)


    def test_empty_calibration_path_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, _ = self.run_live_rubric(Path(temporary), calibration="")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("calibration is required", result.stderr)


    def test_post_plan_calibration_or_fixture_drift_exits_two(self) -> None:
        for drift_target in ("calibration", "fixture"):
            with self.subTest(drift_target=drift_target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                fixtures = root / "calibration-fixtures.json"
                fixtures.write_text(
                    CALIBRATION_FIXTURES.read_text(encoding="utf-8"), encoding="utf-8"
                )
                calibration_result, calibration_output = self.run_calibrate(
                    root,
                    extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"},
                    fixtures=fixtures,
                )
                self.assertEqual(calibration_result.returncode, 0, calibration_result.stderr)
                calibration_path = calibration_output / "calibration.json"
                skill = self.make_skill(root)
                tasks = root / "tasks.jsonl"
                tasks.write_text(
                    json.dumps(
                        {
                            "id": "choice",
                            "prompt": "Choose Blue.",
                            "graders": [
                                {"type": "response_not_empty"},
                                {
                                    "type": "rubric",
                                    "dimensions": [
                                        {
                                            "name": "safe choice",
                                            "levels": [
                                                {
                                                    "name": "not_met",
                                                    "description": "Does not choose Blue.",
                                                },
                                                {
                                                    "name": "met",
                                                    "description": "Chooses Blue.",
                                                },
                                            ],
                                        }
                                    ],
                                },
                            ],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                spec = importlib.util.spec_from_file_location("skill_eval_loop_task8", EVALUATOR)
                self.assertIsNotNone(spec)
                self.assertIsNotNone(spec.loader)
                evaluator = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(evaluator)
                arguments = [
                    "skill-eval-loop",
                    "run",
                    "--skill",
                    str(skill),
                    "--tasks",
                    str(tasks),
                    "--output",
                    str(root / "run"),
                    "--harness",
                    "codex",
                    "--harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "gpt-5.6-terra",
                    "--judge-model",
                    "gpt-5.6-sol",
                    "--calibration",
                    str(calibration_path),
                    "--timeout-seconds",
                    "1",
                ]
                original_run_live = evaluator.run_live

                def drift_then_run(current_plan: dict[str, object]) -> dict[str, object]:
                    drift_path = calibration_path if drift_target == "calibration" else fixtures
                    drift_path.write_text(
                        drift_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
                    )
                    return original_run_live(current_plan)

                with patch.object(evaluator, "run_live", side_effect=drift_then_run):
                    with patch.object(evaluator.sys, "argv", arguments):
                        self.assertEqual(evaluator.main(), 2)


    def test_calibrate_dry_run_validates_fixtures_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, output = self.run_calibrate(Path(temporary), dry_run=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertTrue(plan["valid"])
            self.assertFalse(plan["created_artifacts"])
            self.assertEqual(plan["counts"]["total_invocations"], 3)
            self.assertEqual(
                [case["id"] for case in plan["suite"]["cases"]],
                ["known-better", "known-worse", "tie"],
            )
            self.assertTrue(all(case["rationale"] for case in plan["suite"]["cases"]))
            self.assertFalse(output.exists())


    def test_calibrate_accepts_when_judge_matches_locked_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, output = self.run_calibrate(
                Path(temporary), extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"}
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertTrue(summary["valid"])
            self.assertTrue(summary["accepted"])
            self.assertEqual(summary["agreements"], 3)
            self.assertEqual(summary["disagreements"], [])
            self.assertEqual(summary["usage"]["measured_invocations"], 3)
            self.assertEqual(summary["usage"]["total_tokens"], 39)
            retained = json.loads((output / "calibration.json").read_text(encoding="utf-8"))
            self.assertEqual(retained["accepted"], True)


    def test_calibrate_fails_fast_after_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invocation_log = root / "invocations.txt"
            result, output = self.run_calibrate(
                root,
                extra_env={
                    "SIMPLE_FAKE_INFRA_FAILURE": "1",
                    "SIMPLE_FAKE_INVOCATION_LOG": str(invocation_log),
                },
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(invocation_log.read_text(encoding="utf-8").splitlines(), ["pairwise"])
            self.assertIn("PROGRESS:", result.stderr)
            retained = json.loads((output / "calibration.json").read_text(encoding="utf-8"))
            self.assertEqual(retained["cases"][0]["reason"], "infrastructure_failed")
            self.assertTrue((output / "known-better" / "prompt.txt").is_file())
            prompt = (output / "known-better" / "prompt.txt").read_text(encoding="utf-8")
            self.assertNotIn("better", prompt.split("\n\n", 1)[0])
            self.assertNotIn("control", prompt)
            self.assertNotIn("treatment", prompt)


    def test_calibrate_reports_disagreements_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, output = self.run_calibrate(Path(temporary))

            self.assertEqual(result.returncode, 1, result.stderr)
            summary = json.loads(result.stdout)
            self.assertTrue(summary["valid"])
            self.assertFalse(summary["accepted"])
            self.assertEqual(
                [item["id"] for item in summary["disagreements"]],
                ["known-better", "tie"],
            )
            self.assertEqual(summary["disagreements"][0]["human_winner"], "better")
            self.assertEqual(summary["disagreements"][0]["judge_winner"], "other")
            self.assertTrue(summary["disagreements"][0]["rationale"])
            retained = json.loads((output / "calibration.json").read_text(encoding="utf-8"))
            self.assertFalse(retained["accepted"])


    def test_calibration_rejects_uncovered_task_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                json.dumps(
                    {
                        "id": "waterfall",
                        "prompt": "Review this snippet.",
                        "graders": [
                            {"type": "response_not_empty"},
                            {
                                "type": "rubric",
                                "dimensions": [
                                    {
                                        "name": "waterfall_diagnosis",
                                        "levels": [
                                            {
                                                "name": "not_met",
                                                "description": "Misses the waterfall.",
                                            },
                                            {
                                                "name": "met",
                                                "description": "Identifies the waterfall.",
                                            },
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            calibration_result, calibration_output = self.run_calibrate(
                root, extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"}
            )
            self.assertEqual(calibration_result.returncode, 0, calibration_result.stderr)
            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(tasks),
                "--output",
                str(root / "out"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "gpt-5.6-terra",
                "--judge-model",
                "gpt-5.6-sol",
                "--calibration",
                str(calibration_output / "calibration.json"),
                "--dry-run",
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("omit rubric dimensions", result.stderr)
            self.assertIn("waterfall_diagnosis", result.stderr)


    def test_react_v2_tasks_require_react_review_calibration_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            calibration_result, calibration_output = self.run_calibrate(
                root, extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"}
            )
            self.assertEqual(calibration_result.returncode, 0, calibration_result.stderr)
            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(Path(__file__).resolve().parents[1] / "tasks" / "react-best-practices-v2.jsonl"),
                "--output",
                str(root / "out"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "gpt-5.6-terra",
                "--judge-model",
                "gpt-5.6-sol",
                "--calibration",
                str(calibration_output / "calibration.json"),
                "--dry-run",
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("primary_diagnosis", result.stderr)

