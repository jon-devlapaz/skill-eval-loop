import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "skills" / "skill-eval-loop" / "scripts" / "skill_eval_loop.py"
LAUNCHER = ROOT / "skills" / "skill-eval-loop" / "scripts" / "skill-eval-loop"
FAKE_CODEX = ROOT / "tests" / "fixtures" / "simple-fake-codex"
FAKE_MUSE = ROOT / "tests" / "fixtures" / "simple-fake-muse"
FAKE_CURSOR_AGENT = ROOT / "tests" / "fixtures" / "simple-fake-cursor-agent"
FAKE_PI = ROOT / "tests" / "fixtures" / "simple-fake-pi"
FAKE_AGY = ROOT / "tests" / "fixtures" / "simple-fake-agy"
CALIBRATION_FIXTURES = ROOT / "tests" / "fixtures" / "calibration" / "v1.json"


class EvaluatorTestCase(unittest.TestCase):
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
        trials: int = 1,
        promotion: bool = False,
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
                "--trials",
                str(trials),
            ]
            + (["--calibration", str(calibration)] if calibration is not None else [])
            + (["--promotion"] if promotion else []),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        return result, output, output / "task-choice" / "trial-001" / "report.json"


    @staticmethod
    def hash_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()


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

