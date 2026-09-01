"""Harness adapters, live runner isolation, and the public launcher."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from helpers import (
    EVALUATOR,
    FAKE_CODEX,
    LAUNCHER,
    ROOT,
    EvaluatorTestCase,
)


class HarnessTests(EvaluatorTestCase):
    def test_healthcheck_reports_python_commands(self) -> None:
        result = self.run_cli("healthcheck", "--skill-dir", str(EVALUATOR.parents[1]))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["commands"],
            ["healthcheck", "run", "calibrate", "prepare-review", "finalize-review"],
        )


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


    def test_unsupported_harness_rejected_with_supported_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(json.dumps({"id": "t1", "prompt": "p", "graders": [{"type": "response_not_empty"}]}) + "\n", encoding="utf-8")
            result = self.run_cli(
                "run",
                "--skill",
                str(skill),
                "--tasks",
                str(tasks),
                "--output",
                str(root / "out"),
                "--harness",
                "unsupported-agent",
                "--model",
                "test-model",
                "--dry-run",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("unsupported harness 'unsupported-agent'", result.stderr)
            self.assertIn("supported harnesses are: antigravity, claude, codex, cursor-agent, hermes, muse, pi, script", result.stderr)


    def test_all_supported_harness_adapters_build_commands(self) -> None:
        spec = importlib.util.spec_from_file_location("evaluator_mod", EVALUATOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        
        expected_harnesses = ["antigravity", "claude", "codex", "cursor-agent", "hermes", "muse", "pi", "script"]
        self.assertEqual(sorted(evaluator.SUPPORTED_HARNESSES), expected_harnesses)
        
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary)
            for harness_name in expected_harnesses:
                adapter = evaluator.get_harness_adapter(harness_name)
                cmd = adapter.build_command(
                    executable=f"/bin/{harness_name}",
                    model="test-model",
                    prompt="Hello world",
                    workspace=ws,
                    role="treatment",
                    timeout_seconds=30,
                    skill_name="test-skill",
                )
                self.assertIsInstance(cmd, list)
                self.assertTrue(len(cmd) >= 2)
                self.assertIn("test-model", cmd)


    def test_script_harness_live_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = self.make_skill(root)
            tasks = root / "tasks.jsonl"
            tasks.write_text(json.dumps({"id": "t1", "prompt": "say hello", "graders": [{"type": "response_not_empty"}]}) + "\n", encoding="utf-8")
            
            runner_script = root / "custom_runner.py"
            runner_script.write_text(
                '#!/usr/bin/env python3\n'
                'import json, sys\n'
                '# Emits normalized JSON trace on stdout\n'
                'print(json.dumps({\n'
                '  "response": "Hello from custom script runner!",\n'
                '  "model": "script-model-v1",\n'
                '  "input_tokens": 15,\n'
                '  "output_tokens": 8,\n'
                '  "skill_accessed": True\n'
                '}))\n',
                encoding="utf-8",
            )
            runner_script.chmod(0o755)

            output = root / "script-run"
            result = self.run_cli(
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
                str(runner_script),
                "--model",
                "script-model-v1",
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            run_data = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(run_data["valid"])
            self.assertEqual(run_data["quality_status"], "not_required")
            pair_report = json.loads((output / "task-t1" / "trial-001" / "report.json").read_text(encoding="utf-8"))
            treatment_response = (output / "task-t1" / "trial-001" / "treatment" / "response.md").read_text(encoding="utf-8")
            self.assertEqual(treatment_response, "Hello from custom script runner!")
            self.assertTrue(pair_report["activation"]["trace_skill_read"])

