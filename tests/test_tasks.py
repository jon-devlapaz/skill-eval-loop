"""Task loading, dry-run validation, and payload hashing."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

from helpers import (
    EVALUATOR,
    FAKE_CODEX,
    EvaluatorTestCase,
)


class TaskTests(EvaluatorTestCase):
    def test_dry_run_validates_inputs_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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
                                                "description": "Does not choose the safe option.",
                                            },
                                            {
                                                "name": "met",
                                                "description": "Chooses the safe option.",
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
            output = root / "new-run"

            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(tasks),
                "--output",
                str(output),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--judge-model",
                "judge-model",
                "--trials",
                "3",
                "--dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertTrue(plan["valid"])
            self.assertFalse(plan["created_artifacts"])
            self.assertEqual(plan["configuration"]["intervention"], "injected_skill_instructions")
            self.assertEqual(plan["counts"]["total_invocations"], 15)
            self.assertEqual(plan["configuration"]["timeout_seconds"], 300)
            self.assertEqual(
                plan["task_snapshot"][0]["graders"][1]["dimensions"][0]["name"],
                "safe choice",
            )
            self.assertFalse(output.exists())


    def test_dry_run_rejects_rubric_without_response_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"rubric","dimensions":[{"name":"choice","levels":[{"name":"not_met","description":"Wrong."},{"name":"met","description":"Right."}]}]}]}\n',
                encoding="utf-8",
            )

            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(tasks),
                "--output",
                str(root / "new-run"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--judge-model",
                "judge-model",
                "--dry-run",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("require a response_not_empty preflight", result.stderr)


    def test_dry_run_rejects_a_path_unsafe_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                '{"id":"../escape","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )

            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(tasks),
                "--output",
                str(root / "new-run"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--dry-run",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("must be path-safe", result.stderr)


    def test_dry_run_rejects_invalid_rubric_dimensions(self) -> None:
        cases = [
            (
                '{"type":"rubric"}',
                "field dimensions: must be a non-empty array",
            ),
            (
                '{"type":"rubric","dimensions":[{"name":"scope","levels":[{"name":"not_met","description":"No."},{"name":"met","description":"Yes."}]},{"name":"scope","levels":[{"name":"not_met","description":"No."},{"name":"met","description":"Yes."}]}]}',
                "field name: duplicate value 'scope'",
            ),
            (
                '{"type":"rubric","dimensions":[{"name":"scope","levels":[{"name":"met","description":"Yes."}]}]}',
                "field levels: must contain at least two entries",
            ),
        ]
        for rubric, expected_error in cases:
            with self.subTest(expected_error=expected_error), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                skill = self.make_skill(root)
                tasks = root / "tasks.jsonl"
                tasks.write_text(
                    '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"response_not_empty"},'
                    + rubric
                    + "]}\n",
                    encoding="utf-8",
                )

                result = self.run_cli(
                    "run",
                    "--skill",
                    str(skill),
                    "--tasks",
                    str(tasks),
                    "--output",
                    str(root / "new-run"),
                    "--harness",
                    "codex",
                    "--harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "test-model",
                    "--judge-model",
                    "judge-model",
                    "--dry-run",
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn(expected_error, result.stderr)


    def test_dry_run_uses_target_owned_tasks_when_tasks_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            evals = skill / "evals"
            evals.mkdir()
            tasks = evals / "tasks.jsonl"
            tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )

            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--output",
                str(root / "new-run"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["configuration"]["tasks_path"],
                str(tasks.resolve()),
            )


    def test_promotion_requires_explicit_tasks_and_repeated_trials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            evals = skill / "evals"
            evals.mkdir()
            (evals / "tasks.jsonl").write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )

            missing_tasks = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--output",
                str(root / "missing-tasks"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--trials",
                "3",
                "--promotion",
                "--dry-run",
            )

            self.assertEqual(missing_tasks.returncode, 1)
            self.assertIn("explicit independently controlled tasks path", missing_tasks.stderr)

            tasks = evals / "tasks.jsonl"
            too_few_trials = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(tasks),
                "--output",
                str(root / "too-few-trials"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--trials",
                "2",
                "--promotion",
                "--dry-run",
            )

            self.assertEqual(too_few_trials.returncode, 1)
            self.assertIn("at least 3 trials", too_few_trials.stderr)


    def test_promotion_plan_records_role_and_requires_rubric_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            deterministic_tasks = root / "deterministic.jsonl"
            deterministic_tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )

            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(deterministic_tasks),
                "--output",
                str(root / "promotion"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--trials",
                "3",
                "--promotion",
                "--dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["configuration"]["evaluation_role"], "promotion")
            self.assertEqual(plan["counts"]["paired_trials"], 3)

            rubric_tasks = root / "rubric.jsonl"
            rubric_tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"response_not_empty"},{"type":"rubric","dimensions":[{"name":"choice","levels":[{"name":"not_met","description":"Does not choose Blue."},{"name":"met","description":"Chooses Blue."}]}]}]}\n',
                encoding="utf-8",
            )
            uncalibrated = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(rubric_tasks),
                "--output",
                str(root / "uncalibrated-promotion"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "runner-model",
                "--judge-model",
                "judge-model",
                "--trials",
                "3",
                "--promotion",
                "--dry-run",
            )

            self.assertEqual(uncalibrated.returncode, 1)
            self.assertIn("require accepted calibration", uncalibrated.stderr)


    def test_dry_run_requires_explicit_or_target_owned_tasks_before_harness_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            output = root / "new-run"

            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--output",
                str(output),
                "--harness",
                "codex",
                "--harness-bin",
                "/missing-codex",
                "--model",
                "test-model",
                "--dry-run",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("create it with the independent authoring workflow", result.stderr)
            self.assertNotIn("codex executable not found", result.stderr)
            self.assertFalse(output.exists())


    def test_dry_run_rejects_a_grader_path_that_escapes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                '{"id":"escape","prompt":"Check.","graders":[{"type":"file_exists","path":"../secret"}]}\n',
                encoding="utf-8",
            )

            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(tasks),
                "--output",
                str(root / "new-run"),
                "--harness",
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                "test-model",
                "--dry-run",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("must stay inside the trial workspace", result.stderr)


    def test_promotion_rejects_tasks_equal_or_beneath_skill_through_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = skill / "holdout.jsonl"
            tasks.write_text('{"id":"choice","prompt":"Choose.","graders":[{"type":"regex","pattern":"Blue"}]}\n')
            alias = root / "alias"
            alias.symlink_to(skill, target_is_directory=True)
            result = self.run_cli("run", "--skill", str(skill), "--tasks", str(alias / tasks.name), "--output", str(root / "out"), "--harness", "codex", "--harness-bin", str(FAKE_CODEX), "--model", "m", "--trials", "3", "--promotion", "--dry-run")
            self.assertEqual(result.returncode, 1)
            self.assertIn("outside the target skill", result.stderr)


    def test_task_ids_collide_after_unicode_normalization_and_casefold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text("\n".join([
                '{"id":"Café","prompt":"One.","graders":[{"type":"regex","pattern":"x"}]}',
                '{"id":"café","prompt":"Two.","graders":[{"type":"regex","pattern":"x"}]}',
            ]) + "\n", encoding="utf-8")
            result = self.run_cli("run", "--skill", str(skill), "--tasks", str(tasks), "--output", str(root / "out"), "--harness", "codex", "--harness-bin", str(FAKE_CODEX), "--model", "m", "--dry-run")
            self.assertEqual(result.returncode, 1)
            self.assertIn("duplicate value", result.stderr)


    def test_tink_source_receipt_is_not_payload_hash_or_treatment_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            spec = importlib.util.spec_from_file_location("skill_eval_loop_receipt", EVALUATOR)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            evaluator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(evaluator)
            before = evaluator.hash_skill(skill)
            (skill / ".tink-source.json").write_text('{"managed":true}\n', encoding="utf-8")
            self.assertEqual(before, evaluator.hash_skill(skill))
            destination = root / "copied"
            evaluator.copy_skill_payload(skill, destination)
            self.assertFalse((destination / ".tink-source.json").exists())


    def test_evals_and_tests_are_not_payload_hash_or_treatment_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            spec = importlib.util.spec_from_file_location("skill_eval_loop_payload", EVALUATOR)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            evaluator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(evaluator)
            before = evaluator.hash_skill(skill)
            for directory in ("evals", "tests"):
                excluded = skill / directory
                excluded.mkdir()
                (excluded / "extra.txt").write_text("ignored\n", encoding="utf-8")
            self.assertEqual(before, evaluator.hash_skill(skill))
            destination = root / "copied"
            evaluator.copy_skill_payload(skill, destination)
            self.assertFalse((destination / "evals").exists())
            self.assertFalse((destination / "tests").exists())


    def test_sqlite_journals_excluded_while_databases_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            spec = importlib.util.spec_from_file_location("skill_eval_loop_payload_db", EVALUATOR)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            evaluator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(evaluator)

            before = evaluator.hash_skill(skill)
            (skill / "data.db").write_bytes(b"database-contents")
            after_db = evaluator.hash_skill(skill)
            self.assertNotEqual(before, after_db)

            for suffix in ("-wal", "-shm", "-journal"):
                (skill / f"data.db{suffix}").write_bytes(b"ephemeral-journal")
            self.assertEqual(after_db, evaluator.hash_skill(skill))

            destination = root / "copied"
            evaluator.copy_skill_payload(skill, destination)
            self.assertTrue((destination / "data.db").exists())
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse((destination / f"data.db{suffix}").exists())


    def test_relative_and_home_paths_resolve_in_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(EVALUATOR),
                    "run",
                    "--skill",
                    "~/target-skill",
                    "--tasks",
                    "tasks.jsonl",
                    "--output",
                    "./fresh-run",
                    "--harness",
                    "codex",
                    "--harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "test-model",
                    "--dry-run",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                env={**self.isolated_env(root), "HOME": str(root)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["configuration"]["skill_path"], str(skill.resolve()))
            self.assertEqual(plan["configuration"]["tasks_path"], str(tasks.resolve()))
            self.assertEqual(
                plan["configuration"]["output_dir"],
                str((root / "fresh-run").resolve()),
            )


    def test_react_v2_quality_suite_dry_run_without_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = Path(__file__).resolve().parents[1] / "tasks" / "react-best-practices-v2.jsonl"
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
                "test-model",
                "--judge-model",
                "judge-model",
                "--dry-run",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["counts"]["task_count"], 8)
            self.assertEqual(plan["counts"]["total_invocations"], 40)
            self.assertEqual(plan["configuration"]["calibration_status"], "not_run")
            names = {
                dimension["name"]
                for task in plan["task_snapshot"]
                for grader in task["graders"]
                if grader["type"] == "rubric"
                for dimension in grader["dimensions"]
            }
            self.assertEqual(names, {"primary_diagnosis", "actionable_fix", "grounded_claims"})
