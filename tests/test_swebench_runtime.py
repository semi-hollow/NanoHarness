import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.bench.adapters.case_runtime import LocalCaseExecutor, render_case_task
from agent_forge.bench.adapters.git_workspace import SwebenchWorkspaceManager
from agent_forge.bench.adapters.official_evaluator import SwebenchOfficialEvaluator
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult, BenchRunSummary


class SwebenchRuntimeTest(unittest.TestCase):
    def test_case_task_requires_focused_pytest_diagnostics(self) -> None:
        task = render_case_task(BenchCase("case-1", "owner/repo", "abc123", "Fix it"))

        self.assertIn("python_validation with check_type=pytest", task)
        self.assertIn("smallest relevant existing test path or pytest node id", task)
        self.assertIn("check_type=unittest only for unittest suites", task)
        self.assertIn("allowlisted run_command fallback", task)

    def test_benchmark_case_uses_isolated_active_workspace_and_cleans_it_up(
        self,
    ) -> None:
        captured: dict[str, int] = {}

        class Config:
            def is_configured(self) -> bool:
                return True

        class FakeAgentLoop:
            def __init__(self, config, trace, registry, llm) -> None:
                self.workspace = Path(config.workspace)
                captured["runtime_timeout"] = config.tool_execution_timeout_seconds
                captured["run_command_timeout"] = registry.get(
                    "run_command"
                ).timeout_seconds
                captured["python_validation_timeout"] = registry.get(
                    "python_validation"
                ).timeout_seconds

            def run(self, task: str) -> str:
                (self.workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
                return "candidate patch generated"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=source,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"], cwd=source, check=True
            )
            (source / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=source, check=True)
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=source,
                check=True,
                capture_output=True,
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            case = BenchCase("local__case-1", str(source), head, "Change the value")
            manager = SwebenchWorkspaceManager(root / "cache", root / "bench")
            output = root / "output"

            with (
                patch(
                    "agent_forge.bench.adapters.case_runtime.resolve_llm_config",
                    return_value=Config(),
                ),
                patch(
                    "agent_forge.bench.adapters.case_runtime.build_llm",
                    return_value=object(),
                ),
                patch(
                    "agent_forge.bench.adapters.case_runtime.build_agent_loop",
                    side_effect=lambda config, _trace, registry, _llm: FakeAgentLoop(
                        config, None, registry, None
                    ),
                ),
            ):
                result = LocalCaseExecutor(manager).run(
                    case,
                    case_dir=output / "cases" / case.instance_id,
                    agent_mode="single",
                    request=SwebenchRunRequest(
                        provider="deepseek",
                        model="model",
                        max_steps=2,
                        max_context_chars=1000,
                        tool_execution_timeout_seconds=600,
                        execution_mode="worktree",
                        keep_worktree=False,
                    ),
                )

            self.assertEqual(result.status, "patch_generated")
            self.assertIn(
                "+value = 2", result.candidate_diff_path.read_text(encoding="utf-8")
            )
            self.assertTrue(
                (
                    output / "cases" / "local__case-1" / "execution_environment.json"
                ).exists()
            )
            self.assertFalse(result.workspace.exists())
            self.assertEqual(captured["runtime_timeout"], 600)
            self.assertEqual(captured["run_command_timeout"], 600)
            self.assertEqual(captured["python_validation_timeout"], 600)

    def test_official_eval_process_failure_is_not_patch_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.json"
            candidate_diff_path = root / "candidate_changes.diff"
            trace.write_text("{}", encoding="utf-8")
            candidate_diff_path.write_text("diff", encoding="utf-8")
            case = BenchCaseResult(
                instance_id="case-1",
                repo="local/repo",
                workspace=root,
                trace_path=trace,
                usage_report_path=None,
                candidate_diff_path=candidate_diff_path,
                status="patch_generated",
                final_answer="candidate",
                patch_chars=4,
            )
            summary = BenchRunSummary(
                run_id="run-1",
                dataset_name="local",
                split="test",
                provider="deepseek",
                model="default",
                output_dir=root,
                predictions_path=root / "predictions.jsonl",
                case_results=[case],
            )
            with (
                patch(
                    "agent_forge.bench.adapters.official_evaluator.importlib.util.find_spec",
                    return_value=True,
                ),
                patch(
                    "agent_forge.bench.adapters.official_evaluator.subprocess.run"
                ) as run,
            ):
                run.return_value.returncode = 2
                run.return_value.stdout = ""
                run.return_value.stderr = "docker failed"
                SwebenchOfficialEvaluator().evaluate(
                    summary,
                    SwebenchRunRequest(max_workers=1, namespace_empty=False),
                )
            self.assertEqual(case.evaluation_status, "official_eval_error")
            self.assertIn("docker failed", summary.official_eval_output)


if __name__ == "__main__":
    unittest.main()
