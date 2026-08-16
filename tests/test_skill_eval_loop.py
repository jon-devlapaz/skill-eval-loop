import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "skills" / "skill-eval-loop" / "scripts" / "skill_eval_loop.py"
LAUNCHER = ROOT / "skills" / "skill-eval-loop" / "scripts" / "skill-eval-loop"
FAKE_CODEX = ROOT / "tests" / "fixtures" / "simple-fake-codex"
CALIBRATION_FIXTURES = ROOT / "tests" / "fixtures" / "calibration" / "v1.json"


class SkillEvalLoopCliTests(unittest.TestCase):
    def make_skill(self, root: Path) -> Path:
        skill = root / "target-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: target-skill\n---\n", encoding="utf-8")
        return skill

    def isolated_env(self, root: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
        home = root / "user-home"
        home.mkdir(exist_ok=True)
        environment = {**os.environ, "HOME": str(home)}
        environment.pop("CODEX_HOME", None)
        if extra:
            environment.update(extra)
        return environment

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(EVALUATOR), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_live_rubric(
        self,
        root: Path,
        *,
        runner_model: str = "gpt-5.6-terra",
        judge_model: str = "gpt-5.6-sol",
        extra_env: dict[str, str] | None = None,
        control_response: str = "Blue",
        calibration: Path | str | None = None,
        use_calibration: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
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
        output = root / "run"
        environment = self.isolated_env(
            root,
            {
                "SIMPLE_FAKE_CONTROL_RESPONSE": control_response,
                **(extra_env or {}),
            },
        )
        if use_calibration and calibration is None and runner_model != judge_model:
            calibration_result, calibration_output = self.run_calibrate(
                root, extra_env={"SIMPLE_FAKE_PAIRWISE_COMPARE": "1"},
                judge_model=judge_model,
            )
            self.assertEqual(calibration_result.returncode, 0, calibration_result.stderr)
            calibration = calibration_output / "calibration.json"
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
                "codex",
                "--harness-bin",
                str(FAKE_CODEX),
                "--model",
                runner_model,
                "--judge-model",
                judge_model,
                "--timeout-seconds",
                "1",
            ] + (["--calibration", str(calibration)] if calibration is not None else []),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        return result, output, output / "task-choice" / "trial-001" / "report.json"

    def test_healthcheck_reports_python_commands(self) -> None:
        result = self.run_cli("healthcheck", "--skill-dir", str(EVALUATOR.parents[1]))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["commands"], ["healthcheck", "run", "calibrate"])

    def test_public_launcher_needs_only_python3(self) -> None:
        result = subprocess.run(
            [str(LAUNCHER), "healthcheck", "--skill-dir", str(EVALUATOR.parents[1])],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["valid"])

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
            self.assertEqual(json.loads(result.stdout)["configuration"]["tasks_path"], str(tasks))

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

    def test_live_run_retains_control_and_treatment_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )
            output = root / "run"
            cwd_log = root / "runner-cwds.txt"
            host_skill = root / "user-home" / ".codex" / "skills" / "target-skill"
            host_skill.mkdir(parents=True)
            (host_skill / "SKILL.md").write_text("---\nname: target-skill\n---\n", encoding="utf-8")
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
                    "codex",
                    "--harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "test-model",
                    "--trials",
                    "2",
                    "--timeout-seconds",
                    "5",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env=self.isolated_env(root, {"SIMPLE_FAKE_CWD_LOG": str(cwd_log)}),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["valid"])
            self.assertEqual(report["quality_status"], "not_required")
            self.assertEqual(report["activation"]["status"], "observed")
            self.assertEqual(report["calibration_status"], "not_run")
            self.assertEqual(len(report["pairs"]), 2)
            self.assertEqual(report["pairs"][0]["execution_order"], ["control", "treatment"])
            self.assertEqual(report["pairs"][1]["execution_order"], ["treatment", "control"])
            first_pair = output / "task-choice" / "trial-001"
            self.assertTrue((first_pair / "report.json").is_file())
            self.assertTrue((first_pair / "control" / "response.md").is_file())
            self.assertTrue((first_pair / "treatment" / "response.md").is_file())
            pair_report = json.loads((first_pair / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(pair_report["runner_valid"])
            self.assertEqual(pair_report["intervention"], "injected_skill_instructions")
            self.assertEqual(pair_report["quality_status"], "not_required")
            self.assertEqual(pair_report["quality_outcome"], "not_judged")
            self.assertEqual(pair_report["activation"]["status"], "observed")
            self.assertEqual(pair_report["calibration_status"], "not_run")
            self.assertEqual(pair_report["deterministic_comparison"], "treatment_only")
            self.assertTrue(pair_report["isolation"]["control_skill_absent"])
            self.assertTrue(pair_report["isolation"]["treatment_skill_present"])
            self.assertTrue(
                pair_report["isolation"]["treatment_installed_source_hash_match"]
            )
            self.assertFalse((output / "codex-home").exists())
            self.assertNotIn("auth.json", (first_pair / "report.json").read_text(encoding="utf-8"))
            markdown = (first_pair / "report.md").read_text(encoding="utf-8")
            self.assertIn("Intervention: injected_skill_instructions", markdown)
            self.assertIn("Semantic quality was not judged.", markdown)
            self.assertIn("Activation: observed (skill_instructions_injected)", markdown)
            control_stderr = (first_pair / "control" / "stderr.txt").read_text(encoding="utf-8")
            treatment_stderr = (first_pair / "treatment" / "stderr.txt").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("<skill_instructions", control_stderr)
            self.assertIn('<skill_instructions name="target-skill"', treatment_stderr)
            self.assertIn("<task>\nChoose Blue.\n</task>", treatment_stderr)
            runner_cwds = [Path(item) for item in cwd_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(runner_cwds), 4)
            self.assertTrue(all(ROOT not in path.parents for path in runner_cwds))
            self.assertTrue(all(output not in path.parents for path in runner_cwds))
            self.assertTrue(all(not path.exists() for path in runner_cwds))

    def test_live_run_copies_host_auth_json_only_during_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )
            host_auth = root / "user-home" / ".codex" / "auth.json"
            host_auth.parent.mkdir(parents=True)
            host_auth.write_text('{"OPENAI_API_KEY":"secret"}\n', encoding="utf-8")
            auth_log = root / "auth-log.txt"
            output = root / "run"
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
                    "codex",
                    "--harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "test-model",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env=self.isolated_env(root, {"SIMPLE_FAKE_AUTH_LOG": str(auth_log)}),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(set(auth_log.read_text(encoding="utf-8").splitlines()), {"present"})
            self.assertFalse((output / "codex-home").exists())
            report_text = (output / "task-choice" / "trial-001" / "report.json").read_text(encoding="utf-8")
            self.assertNotIn("secret", report_text)
            self.assertNotIn("auth.json", report_text)

    def test_live_run_discards_auth_when_initialization_fails(self) -> None:
        for failure in ("config", "tasks"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                skill = self.make_skill(root)
                tasks = root / "tasks.jsonl"
                tasks.write_text(
                    '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                    encoding="utf-8",
                )
                user_home = root / "user-home"
                host_auth = user_home / ".codex" / "auth.json"
                host_auth.parent.mkdir(parents=True)
                host_auth.write_text('{"OPENAI_API_KEY":"secret"}\n', encoding="utf-8")
                output = root / "run"
                spec = importlib.util.spec_from_file_location(
                    f"skill_eval_loop_auth_cleanup_{failure}", EVALUATOR
                )
                self.assertIsNotNone(spec)
                self.assertIsNotNone(spec.loader)
                evaluator = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(evaluator)
                arguments = evaluator.parser().parse_args(
                    [
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
                    ]
                )
                plan = evaluator.build_plan(arguments)

                with patch.dict(os.environ, {"HOME": str(user_home)}):
                    if failure == "config":
                        with patch.object(
                            evaluator, "write_json", side_effect=OSError("config write failed")
                        ):
                            with self.assertRaisesRegex(OSError, "config write failed"):
                                evaluator.run_live(plan)
                    else:
                        original_copyfile = evaluator.shutil.copyfile

                        def fail_task_copy(source: Path, destination: Path) -> None:
                            if Path(destination) == output / "tasks.jsonl":
                                raise OSError("task copy failed")
                            original_copyfile(source, destination)

                        with patch.object(
                            evaluator.shutil, "copyfile", side_effect=fail_task_copy
                        ):
                            with self.assertRaisesRegex(OSError, "task copy failed"):
                                evaluator.run_live(plan)

                self.assertFalse((output / "codex-home").exists())

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

    def test_live_run_marks_model_mismatch_invalid_and_preserves_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                '{"id":"choice","prompt":"Choose Blue.","graders":[{"type":"regex","pattern":"Blue"}]}\n',
                encoding="utf-8",
            )
            output = root / "run"
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
                    "codex",
                    "--harness-bin",
                    str(FAKE_CODEX),
                    "--model",
                    "test-model",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env=self.isolated_env(root, {"SIMPLE_FAKE_REPORTED_MODEL": "different-model"}),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(json.loads(result.stdout)["valid"])
            pair_report = json.loads(
                (output / "task-choice" / "trial-001" / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(pair_report["runner_valid"])
            self.assertTrue((output / "task-choice" / "trial-001" / "control" / "trace.jsonl").is_file())

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

    def test_all_codex_roles_use_cleaned_workspaces_outside_retained_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cwd_log = root / "role-cwds.txt"

            result, output, _ = self.run_live_rubric(
                root,
                extra_env={"SIMPLE_FAKE_ROLE_CWD_LOG": str(cwd_log)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            entries = [line.split("\t", 1) for line in cwd_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [role for role, _ in entries],
                ["runner", "runner", "judge", "judge", "pairwise"],
            )
            workspaces = [Path(path).resolve() for _, path in entries]
            self.assertTrue(all(ROOT.resolve() not in workspace.parents for workspace in workspaces))
            self.assertTrue(all(output.resolve() not in workspace.parents for workspace in workspaces))
            self.assertTrue(all(not workspace.exists() for workspace in workspaces))

    def test_codex_runtime_is_the_shared_target_and_judge_test_surface(self) -> None:
        spec = importlib.util.spec_from_file_location("skill_eval_loop_runtime", EVALUATOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            pair_dir = root / "retained" / "task-choice" / "trial-001"
            codex_home = root / "codex-home"
            codex_home.mkdir()
            cwd_log = root / "runtime-cwds.txt"
            runtime = evaluator.CodexRuntime(
                codex_home,
                {
                    "harness_executable": str(FAKE_CODEX),
                    "model": "runner-model",
                    "judge_model": "judge-model",
                    "timeout_seconds": 1,
                },
            )
            task = {
                "id": "choice",
                "prompt": "Choose Blue.",
                "graders": [{"type": "response_not_empty"}],
            }

            with patch.dict(
                os.environ,
                {"SIMPLE_FAKE_ROLE_CWD_LOG": str(cwd_log)},
                clear=False,
            ):
                control, control_isolation = runtime.run_condition(
                    condition="control",
                    pair_dir=pair_dir,
                    skill=skill,
                    skill_hash=evaluator.hash_skill(skill),
                    skill_name=skill.name,
                    task=task,
                )
                treatment, treatment_isolation = runtime.run_condition(
                    condition="treatment",
                    pair_dir=pair_dir,
                    skill=skill,
                    skill_hash=evaluator.hash_skill(skill),
                    skill_name=skill.name,
                    task=task,
                )
                judgment, _ = runtime.invoke_judge(
                    judge_dir=pair_dir / "judge-001",
                    artifact_root=pair_dir,
                    prompt="Judge this response.",
                    role="judge",
                )

            self.assertEqual(control["execution"]["status"], "completed")
            self.assertEqual(treatment["execution"]["status"], "completed")
            self.assertTrue(control_isolation["control_skill_absent"])
            self.assertTrue(treatment_isolation["treatment_hash_matches"])
            self.assertEqual(judgment["reason"], "")
            self.assertEqual(
                judgment["artifacts"]["prompt"],
                "judge-001/prompt.txt",
            )
            entries = [line.split("\t", 1) for line in cwd_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([role for role, _ in entries], ["runner", "runner", "judge"])
            self.assertTrue(all(not Path(path).exists() for _, path in entries))

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

    def test_trace_records_successful_target_skill_read_as_activation(self) -> None:
        spec = importlib.util.spec_from_file_location("skill_eval_loop_activation", EVALUATOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)

        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / "trace.jsonl"
            trace.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": "sed -n '1,200p' .agents/skills/target-skill/SKILL.md",
                            "exit_code": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            observed = evaluator.parse_trace(trace, skill_name="target-skill")

        self.assertTrue(observed["skill_accessed"])

    def test_trace_records_skill_read_when_later_compound_command_fails(self) -> None:
        spec = importlib.util.spec_from_file_location("skill_eval_loop_activation", EVALUATOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)

        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / "trace.jsonl"
            trace.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": (
                                "sed -n '1,200p' .agents/skills/target-skill/SKILL.md "
                                "&& sed -n '1,200p' missing.md"
                            ),
                            "aggregated_output": (
                                "---\nname: target-skill\ndescription: Test skill.\n---\n"
                                "sed: missing.md: No such file or directory\n"
                            ),
                            "exit_code": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            observed = evaluator.parse_trace(trace, skill_name="target-skill")

        self.assertTrue(observed["skill_accessed"])

    def test_trace_does_not_treat_skill_directory_listing_as_activation(self) -> None:
        spec = importlib.util.spec_from_file_location("skill_eval_loop_activation", EVALUATOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)

        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / "trace.jsonl"
            trace.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": "find .agents/skills/target-skill -maxdepth 1 -type f",
                            "exit_code": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            observed = evaluator.parse_trace(trace, skill_name="target-skill")

        self.assertFalse(observed["skill_accessed"])

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
            self.assertIn("calibration path must be absolute", result.stderr)

    def test_empty_calibration_path_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, _ = self.run_live_rubric(Path(temporary), calibration="")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("calibration path must be absolute", result.stderr)

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

    def run_calibrate(
        self,
        root: Path,
        *,
        extra_env: dict[str, str] | None = None,
        dry_run: bool = False,
        judge_model: str = "gpt-5.6-sol",
        fixtures: Path = CALIBRATION_FIXTURES,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        output = root / "calibration-run"
        arguments = [
            "python3",
            str(EVALUATOR),
            "calibrate",
            "--fixtures",
            str(fixtures),
            "--output",
            str(output),
            "--harness",
            "codex",
            "--harness-bin",
            str(FAKE_CODEX),
            "--model",
            "gpt-5.6-terra",
            "--judge-model",
            judge_model,
            "--timeout-seconds",
            "1",
        ]
        if dry_run:
            arguments.append("--dry-run")
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=self.isolated_env(root, extra_env),
        )
        return result, output

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


if __name__ == "__main__":
    unittest.main()
