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

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(EVALUATOR), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

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
                            {"type": "regex", "pattern": "Blue"},
                            {"type": "rubric", "text": "Choose the safe option."},
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
            self.assertFalse(output.exists())

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
            codex_home = root / "codex-home"
            codex_home.mkdir()
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
                env={**os.environ, "CODEX_HOME": str(codex_home)},
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
            codex_home = root / "codex-home"
            codex_home.mkdir()
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
                env={
                    **os.environ,
                    "CODEX_HOME": str(codex_home),
                    "SIMPLE_FAKE_REPORTED_MODEL": "different-model",
                },
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(json.loads(result.stdout)["valid"])
            pair_report = json.loads(
                (output / "task-choice" / "trial-001" / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(pair_report["runner_valid"])
            self.assertTrue((output / "task-choice" / "trial-001" / "control" / "trace.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
