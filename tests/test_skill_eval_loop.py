import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "skills" / "skill-eval-loop" / "scripts" / "skill_eval_loop.py"
LAUNCHER = ROOT / "skills" / "skill-eval-loop" / "scripts" / "skill-eval-loop"
FAKE_CODEX = ROOT / "tests" / "fixtures" / "simple-fake-codex"


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
            ],
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
        self.assertEqual(json.loads(result.stdout)["commands"], ["healthcheck", "run"])

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
            self.assertEqual(plan["counts"]["total_invocations"], 12)
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
                env=self.isolated_env(root),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["valid"])
            self.assertEqual(len(report["pairs"]), 2)
            self.assertEqual(report["pairs"][0]["execution_order"], ["control", "treatment"])
            self.assertEqual(report["pairs"][1]["execution_order"], ["treatment", "control"])
            first_pair = output / "task-choice" / "trial-001"
            self.assertTrue((first_pair / "report.json").is_file())
            self.assertTrue((first_pair / "control" / "response.md").is_file())
            self.assertTrue((first_pair / "treatment" / "response.md").is_file())
            pair_report = json.loads((first_pair / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(pair_report["runner_valid"])
            self.assertEqual(pair_report["deterministic_comparison"], "treatment_only")
            self.assertTrue(pair_report["isolation"]["control_skill_absent"])
            self.assertTrue(pair_report["isolation"]["treatment_skill_present"])
            self.assertTrue(
                pair_report["isolation"]["treatment_installed_source_hash_match"]
            )
            self.assertTrue((output / "codex-home").is_dir())
            self.assertFalse((output / "codex-home" / "auth.json").exists())
            self.assertNotIn("auth.json", (first_pair / "report.json").read_text(encoding="utf-8"))

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

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(auth_log.read_text(encoding="utf-8").splitlines()), {"present"})
            self.assertTrue((output / "codex-home").is_dir())
            self.assertFalse((output / "codex-home" / "auth.json").exists())
            report_text = (output / "task-choice" / "trial-001" / "report.json").read_text(encoding="utf-8")
            self.assertNotIn("secret", report_text)
            self.assertNotIn("auth.json", report_text)

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

            self.assertEqual(result.returncode, 1, result.stderr)
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
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["rubric_status"], "provisional_non_independent")
            for condition in report["conditions"]:
                judgment = condition["rubric_judgments"][0]
                self.assertEqual(judgment["status"], "provisional_non_independent")
                self.assertEqual(judgment["dimensions"][0]["level"], "met")
                self.assertEqual(judgment["execution"]["requested_model"], "gpt-5.6-sol")
                self.assertEqual(judgment["execution"]["trace_reported_model"], "gpt-5.6-sol")
                judge_dir = output / "task-choice" / "trial-001" / condition["name"] / "judge-001"
                self.assertTrue((judge_dir / "trace.jsonl").is_file())
                self.assertTrue((judge_dir / "response.txt").is_file())

    def test_live_rubric_judge_fails_closed_for_bad_output_or_identity(self) -> None:
        cases = [
            ({"SIMPLE_FAKE_JUDGE_RESPONSE": "not-json"}, "malformed_output"),
            ({"SIMPLE_FAKE_JUDGE_OMIT_MODEL": "1"}, "model_identity_missing"),
            ({"SIMPLE_FAKE_JUDGE_REPORTED_MODEL": "gpt-5.4"}, "model_identity_mismatch"),
            ({"SIMPLE_FAKE_JUDGE_SLEEP_SECONDS": "2"}, "timed_out"),
        ]
        for environment, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason), tempfile.TemporaryDirectory() as temporary:
                result, _, report_path = self.run_live_rubric(
                    Path(temporary), extra_env=environment
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["rubric_status"], "unknown")
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

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["rubric_status"], "unknown")
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

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["rubric_status"], "unknown")
            self.assertEqual(
                report["conditions"][0]["rubric_judgments"][0]["reason"],
                "deterministic_gate_failed",
            )
            self.assertEqual(invocation_log.read_text(encoding="utf-8").splitlines(), ["runner", "runner"])


if __name__ == "__main__":
    unittest.main()
