"""Rubric and pairwise judging."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

from helpers import (
    EVALUATOR,
    FAKE_CODEX,
    ROOT,
    EvaluatorTestCase,
)


class JudgingTests(EvaluatorTestCase):
    def test_live_rubric_judge_retains_structured_evidence_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, output, report_path = self.run_live_rubric(Path(temporary))

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertTrue(summary["valid"])
            self.assertEqual(summary["quality_status"], "provisional_non_independent")
            self.assertEqual(summary["usage"]["measured_invocations"], 5)
            self.assertEqual(summary["usage"]["total_tokens"], 65)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["usage"], summary["usage"])
            self.assertEqual(report["rubric_status"], "provisional_non_independent")
            self.assertEqual(report["pairwise_status"], "provisional_non_independent")
            self.assertEqual(report["quality_status"], "provisional_non_independent")
            self.assertEqual(report["quality_outcome"], report["pairwise"][0]["winner_condition"])
            self.assertEqual(report["activation"]["status"], "observed")
            self.assertEqual(report["calibration_status"], "accepted")
            self.assertIsNotNone(report["fixtures_sha256"])
            names = {item["name"] for item in report["dimension_results"]}
            self.assertEqual(names, {"safe choice"})
            markdown = (output / "task-choice" / "trial-001" / "report.md").read_text(encoding="utf-8")
            self.assertIn("control / safe choice: met", markdown)
            self.assertIn("treatment / safe choice: met", markdown)
            self.assertIn("pairwise / safe choice:", markdown)
            pairwise = report["pairwise"][0]
            self.assertEqual(pairwise["status"], "provisional_non_independent")
            self.assertEqual(pairwise["winner_label"], "A")
            self.assertEqual(pairwise["winner_condition"], pairwise["mapping"]["A"])
            self.assertEqual(set(pairwise["mapping"].values()), {"control", "treatment"})
            prompt = (output / "task-choice" / "trial-001" / "pairwise-001" / "prompt.txt").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("control", prompt)
            self.assertNotIn("treatment", prompt)
            payload = json.loads(prompt.split("\n\n", 1)[1])
            self.assertEqual(
                set(payload),
                {"task_prompt", "candidate_A", "candidate_B", "dimensions"},
            )
            pair_dir = output / "task-choice" / "trial-001"
            for condition in report["conditions"]:
                judgment = condition["rubric_judgments"][0]
                self.assertEqual(judgment["status"], "provisional_non_independent")
                self.assertEqual(judgment["dimensions"][0]["level"], "met")
                self.assertEqual(judgment["execution"]["requested_model"], "gpt-5.6-sol")
                self.assertEqual(judgment["execution"]["trace_reported_model"], "gpt-5.6-sol")
                self.assertEqual(judgment["execution"]["model_identity_source"], "trace_reported")
                self.assertEqual(
                    judgment["artifacts"]["prompt"],
                    f"{condition['name']}/judge-001/prompt.txt",
                )
                for relative in judgment["artifacts"].values():
                    self.assertTrue((pair_dir / relative).is_file(), relative)
            for relative in pairwise["artifacts"].values():
                self.assertTrue((pair_dir / relative).is_file(), relative)


    def test_live_rubric_judge_keeps_missing_trace_model_unattested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, report_path = self.run_live_rubric(
                Path(temporary), extra_env={"SIMPLE_FAKE_JUDGE_OMIT_MODEL": "1"}
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["quality_status"], "provisional_non_independent")
            judgment = report["conditions"][0]["rubric_judgments"][0]
            self.assertEqual(judgment["status"], "provisional_non_independent")
            self.assertEqual(judgment["execution"]["trace_reported_model"], "")
            self.assertEqual(judgment["execution"]["model_identity_source"], "cli_configured")
            self.assertIsNone(judgment["execution"]["model_matches_requested"])


    def test_live_rubric_judge_fails_closed_for_bad_output_or_identity(self) -> None:
        cases = [
            ({"SIMPLE_FAKE_JUDGE_RESPONSE": "not-json"}, "malformed_output"),
            ({"SIMPLE_FAKE_JUDGE_REPORTED_MODEL": "gpt-5.4"}, "model_identity_mismatch"),
            ({"SIMPLE_FAKE_JUDGE_SLEEP_SECONDS": "2"}, "timed_out"),
        ]
        for environment, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason), tempfile.TemporaryDirectory() as temporary:
                result, _, report_path = self.run_live_rubric(
                    Path(temporary), extra_env=environment
                )

                self.assertEqual(result.returncode, 1, result.stderr)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["rubric_status"], "unknown")
                self.assertEqual(report["quality_status"], "unknown")
                self.assertEqual(report["quality_outcome"], "unknown")
                self.assertEqual(
                    report["conditions"][0]["rubric_judgments"][0]["reason"],
                    expected_reason,
                )


    def test_live_rubric_judge_rejects_same_exact_model_without_calling_judge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invocation_log = root / "invocations.txt"
            result, _, report_path = self.run_live_rubric(
                root,
                judge_model="gpt-5.6-terra",
                extra_env={"SIMPLE_FAKE_INVOCATION_LOG": str(invocation_log)},
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["rubric_status"], "unknown")
            self.assertEqual(report["quality_status"], "unknown")
            self.assertEqual(report["conditions"][0]["rubric_judgments"][0]["reason"], "same_model")
            self.assertEqual(invocation_log.read_text(encoding="utf-8").splitlines(), ["runner", "runner"])


    def test_live_rubric_judge_is_skipped_when_deterministic_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invocation_log = root / "invocations.txt"
            result, _, report_path = self.run_live_rubric(
                root,
                extra_env={"SIMPLE_FAKE_INVOCATION_LOG": str(invocation_log)},
                control_response=" ",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["rubric_status"], "unknown")
            self.assertEqual(report["quality_status"], "unknown")
            self.assertEqual(
                report["conditions"][0]["rubric_judgments"][0]["reason"],
                "deterministic_gate_failed",
            )


    def test_live_rubric_judge_runs_when_injected_skill_needs_no_trace_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invocation_log = root / "invocations.txt"
            result, _, report_path = self.run_live_rubric(
                root,
                extra_env={
                    "SIMPLE_FAKE_INVOCATION_LOG": str(invocation_log),
                    "SIMPLE_FAKE_SKIP_SKILL_READ": "1",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["runner_valid"])
            self.assertEqual(report["activation"]["status"], "observed")
            treatment = next(
                condition for condition in report["conditions"] if condition["name"] == "treatment"
            )
            self.assertFalse(treatment["activation"]["trace_skill_read"])
            self.assertEqual(report["quality_status"], "provisional_non_independent")
            self.assertEqual(
                invocation_log.read_text(encoding="utf-8").splitlines(),
                ["runner", "runner", "judge", "judge", "pairwise"],
            )


    def test_pairwise_judge_is_skipped_when_per_output_judgment_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invocation_log = root / "invocations.txt"
            result, output, report_path = self.run_live_rubric(
                root,
                extra_env={
                    "SIMPLE_FAKE_INVOCATION_LOG": str(invocation_log),
                    "SIMPLE_FAKE_JUDGE_RESPONSE": "not-json",
                },
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["rubric_status"], "unknown")
            self.assertEqual(report["pairwise_status"], "unknown")
            self.assertEqual(report["quality_status"], "unknown")
            self.assertEqual(report["quality_outcome"], "unknown")
            self.assertEqual(report["pairwise"][0]["reason"], "per_output_unknown")
            self.assertFalse((output / "task-choice" / "trial-001" / "pairwise-001").exists())
            self.assertEqual(
                invocation_log.read_text(encoding="utf-8").splitlines(),
                ["runner", "runner", "judge", "judge"],
            )


    def test_pairwise_tie_is_complete_quality_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, report_path = self.run_live_rubric(
                Path(temporary),
                extra_env={
                    "SIMPLE_FAKE_PAIRWISE_RESPONSE": json.dumps(
                        {
                            "dimensions": [
                                {
                                    "name": "safe choice",
                                    "evidence": "Both choose Blue.",
                                    "winner": "tie",
                                }
                            ],
                            "winner": "tie",
                        }
                    )
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["quality_status"], "provisional_non_independent")
            self.assertEqual(report["quality_outcome"], "tie")
            self.assertEqual(report["pairwise"][0]["winner_condition"], "tie")


    def test_pairwise_dimension_disagreement_blocks_aggregate_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, output, report_path = self.run_live_rubric(
                Path(temporary),
                extra_env={
                    "SIMPLE_FAKE_PAIRWISE_RESPONSE": json.dumps(
                        {
                            "dimensions": [
                                {
                                    "name": "safe choice",
                                    "evidence": "B is safer.",
                                    "winner": "B",
                                }
                            ],
                            "winner": "A",
                        }
                    )
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            pairwise = report["pairwise"][0]
            self.assertEqual(report["quality_status"], "provisional_non_independent")
            self.assertEqual(report["quality_outcome"], "inconsistent")
            self.assertNotEqual(report["quality_outcome"], pairwise["winner_condition"])
            markdown = (output / "task-choice" / "trial-001" / "report.md").read_text(encoding="utf-8")
            self.assertIn("Quality outcome: inconsistent", markdown)
            self.assertIn("pairwise / safe choice: B", markdown)


    def test_pairwise_tied_dimension_is_compatible_with_aggregate_winner(self) -> None:
        spec = importlib.util.spec_from_file_location("skill_eval_loop_outcome", EVALUATOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)

        outcome = evaluator.quality_outcome_for(
            [
                {
                    "winner_condition": "control",
                    "mapping": {"A": "control", "B": "treatment"},
                    "dimensions": [
                        {"winner": "tie"},
                        {"winner": "A"},
                    ],
                }
            ],
            "provisional_non_independent",
        )

        self.assertEqual(outcome, "control")


    def test_extract_json_payload_handles_markdown_fences(self) -> None:
        spec = importlib.util.spec_from_file_location("evaluator_mod", EVALUATOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)

        raw_json = '{"dimensions": [{"name": "safe choice", "level": "met", "evidence": "good"}]}'
        fenced_json = f"```json\n{raw_json}\n```"
        fenced_plain = f"```\n{raw_json}\n```"

        self.assertEqual(evaluator.extract_json_payload(raw_json), raw_json)
        self.assertEqual(evaluator.extract_json_payload(fenced_json), raw_json)
        self.assertEqual(evaluator.extract_json_payload(fenced_plain), raw_json)
        
        parsed = evaluator.load_judge_json(fenced_json)
        self.assertEqual(parsed["dimensions"][0]["name"], "safe choice")


    def test_markdown_artifact_links_resolve_from_pair_report_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, output, report_path = self.run_live_rubric(Path(temporary))
            self.assertEqual(result.returncode, 0, result.stderr)
            pair_dir = report_path.parent
            markdown = (pair_dir / "report.md").read_text(encoding="utf-8")
            import re
            links = re.findall(r"\]\(([^)]+)\)", markdown)
            self.assertTrue(links)
            self.assertTrue(all((pair_dir / link).is_file() for link in links), links)


    def test_cross_harness_judge_produces_independent_quality_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                json.dumps(
                    {
                        "id": "t1",
                        "prompt": "Choose Blue.",
                        "graders": [
                            {"type": "response_not_empty"},
                            {
                                "type": "rubric",
                                "dimensions": [
                                    {
                                        "name": "safe choice",
                                        "levels": [
                                            {"name": "not_met", "description": "Does not choose Blue."},
                                            {"name": "met", "description": "Chooses Blue."},
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

            # Target harness: script
            target_runner = root / "target_runner.py"
            target_runner.write_text(
                '#!/usr/bin/env python3\n'
                'import json, sys\n'
                'print(json.dumps({"response": "Blue", "model": "gpt-5.6-terra"}))\n',
                encoding="utf-8",
            )
            target_runner.chmod(0o755)

            # Calibrate judge
            cal_res, cal_out = self.run_calibrate(
                root,
                extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"},
                judge_model="gpt-5.6-sol",
            )
            self.assertEqual(cal_res.returncode, 0, cal_res.stderr)
            calibration_path = cal_out / "calibration.json"

            # Judge harness: codex (fake-codex)
            output = root / "cross-run"
            result = subprocess.run(
                [
                    "python3",
                    str(EVALUATOR),
                    "run",
                    "--skill",
                    str(skill),
                    "--tasks",
                    str(tasks),
                    "--output",
                    str(output),
                    "--harness",
                    "script",
                    "--harness-bin",
                    str(target_runner),
                    "--judge-harness",
                    "codex",
                    "--judge-harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "gpt-5.6-terra",
                    "--judge-model",
                    "gpt-5.6-sol",
                    "--calibration",
                    str(calibration_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env=self.isolated_env(root, {"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"}),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            run_data = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(run_data["valid"])
            self.assertEqual(run_data["quality_status"], "independent")
            pair_report = json.loads((output / "task-t1" / "trial-001" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(pair_report["quality_status"], "independent")
            pairwise = pair_report["pairwise"][0]
            self.assertEqual(pairwise["status"], "independent")
            self.assertEqual(pairwise["reason"], "cross_provider_independent_judge")

