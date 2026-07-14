import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.bench.adapters.case_runtime import DirectModelBaseline, LocalCaseExecutor
from agent_forge.bench.adapters.git_workspace import SwebenchWorkspaceManager
from agent_forge.bench.adapters.official_evaluator import SwebenchOfficialEvaluator
from agent_forge.bench.api import run_swebench
from agent_forge.bench.application.swebench import _combined_result
from agent_forge.bench.domain.catalog import REGRESSION_SETS
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult, BenchRunSummary
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.workbench.presentation.http import (
    _latest_multi_agent_summary_path,
    _latest_trace_path,
    _latest_usage_path,
)


class SwebenchCompareTest(unittest.TestCase):
    def test_core_regression_set_has_five_cross_repository_cases(self):
        cases = REGRESSION_SETS["core"]
        self.assertEqual(len(cases), 5)
        self.assertEqual(len({case.split("__", 1)[0] for case in cases}), 5)

    def test_direct_baseline_records_provider_usage_for_scorecards(self):
        class Config:
            def is_configured(self):
                return True

        class Usage:
            def to_dict(self):
                return {
                    "total_tokens": 321,
                    "estimated_cost_usd": 0.012,
                    "latency_ms": 456,
                }

        class LLM:
            last_usage = Usage()

            def chat(self, messages, tools):
                return AgentResponse("diff --git a/a.py b/a.py\n+fixed\n", [])

        case = BenchCase("case-1", "owner/repo", "abc123", "Fix it")
        with patch("agent_forge.bench.adapters.case_runtime.resolve_llm_config", return_value=Config()), patch(
            "agent_forge.bench.adapters.case_runtime.build_llm", return_value=LLM()
        ):
            prediction = DirectModelBaseline().predict(
                case,
                SwebenchRunRequest(provider="provider", model="model"),
            )

        self.assertEqual(prediction["total_tokens"], 321)
        self.assertEqual(prediction["llm_latency_ms"], 456)
        self.assertEqual(prediction["llm_calls"], 1)
        self.assertEqual(prediction["tool_calls"], 0)

    def test_benchmark_case_uses_isolated_active_workspace_and_cleans_it_up(self):
        class Config:
            def is_configured(self):
                return True

        class FakeAgentLoop:
            def __init__(self, config, trace, registry, llm):
                self.workspace = Path(config.workspace)

            def run(self, task):
                (self.workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
                return "candidate patch generated"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=source, check=True)
            (source / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=source, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
            ).stdout.strip()
            case = BenchCase("local__case-1", str(source), head, "Change the value")
            manager = SwebenchWorkspaceManager(root / "cache", root / "bench")
            output = root / "output"

            with patch("agent_forge.bench.adapters.case_runtime.resolve_llm_config", return_value=Config()), patch(
                "agent_forge.bench.adapters.case_runtime.build_llm", return_value=object()
            ), patch(
                "agent_forge.bench.adapters.case_runtime.build_agent_loop",
                side_effect=lambda config, _trace, _registry, _llm: FakeAgentLoop(config, None, None, None),
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
                        execution_mode="worktree",
                        keep_worktree=False,
                    ),
                )

            self.assertEqual(result.status, "patch_generated")
            self.assertIn("+value = 2", result.patch_path.read_text(encoding="utf-8"))
            self.assertTrue((output / "cases" / "local__case-1" / "execution_environment.json").exists())
            self.assertFalse(result.workspace.exists())

    def test_compare_mode_runs_isolated_single_and_multi_variants(self):
        calls = []

        class FakeSource:
            def load(self, request):
                return [
                    BenchCase(
                        instance_id="local__case-1",
                        repo="local/repo",
                        base_commit="abc123",
                        problem_statement="Fix local issue",
                    )
                ]

        class FakeExecutor:
            def run(self, case, *, case_dir, agent_mode, request):
                calls.append((agent_mode, case_dir))
                case_dir.mkdir(parents=True, exist_ok=True)
                patch_path = case_dir / "patch.diff"
                trace_path = case_dir / "trace.json"
                usage_path = case_dir / "usage.json"
                patch_path.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
                trace_path.write_text(json.dumps({"events": []}), encoding="utf-8")
                usage_path.write_text(
                    json.dumps(
                        {
                            "summary": {
                                "estimated_cost_usd": 0.1 if agent_mode == "single" else 0.2,
                                "llm_calls": 1 if agent_mode == "single" else 3,
                                "tool_calls": 2 if agent_mode == "single" else 5,
                                "failed_tool_calls": 0,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                if agent_mode == "multi":
                    multi_dir = case_dir / "multi_agent"
                    multi_dir.mkdir(parents=True, exist_ok=True)
                    (multi_dir / "multi_agent_summary.json").write_text(
                        json.dumps(
                            {
                                "status": "passed",
                                "revision_rounds": 1,
                                "role_results": [
                                    {"role": "Reviewer", "decision": "PASS", "final_answer": "PASS\nlooks good"},
                                    {"role": "Verifier", "decision": "PASS", "final_answer": "PASS\nvalidated"},
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                return BenchCaseResult(
                    instance_id=case.instance_id,
                    repo=case.repo,
                    workspace=case_dir / "workspace",
                    trace_path=trace_path,
                    usage_report_path=case_dir / "usage_report.md",
                    patch_path=patch_path,
                    status="patch_generated",
                    final_answer=f"{agent_mode} done",
                    patch_chars=patch_path.stat().st_size,
                )

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agent_forge.bench.wiring.SwebenchCaseSource", return_value=FakeSource()), patch(
                "agent_forge.bench.wiring.LocalCaseExecutor", return_value=FakeExecutor()
            ):
                summary = run_swebench(
                    SwebenchRunRequest(
                        agent_mode="compare",
                        output_root=tmp,
                        provider="deepseek",
                    )
                )

            self.assertEqual(summary.agent_mode, "compare")
            self.assertEqual([mode for mode, _ in calls], ["single", "multi"])
            self.assertNotEqual(calls[0][1], calls[1][1])
            self.assertEqual(len(summary.case_results), 1)
            comparison_dir = summary.output_dir / "cases" / "local__case-1"
            self.assertTrue((comparison_dir / "comparison.json").exists())
            self.assertTrue((comparison_dir / "evaluation_report.md").exists())
            report = (comparison_dir / "evaluation_report.md").read_text(encoding="utf-8")
            self.assertIn("Single vs Multi-Agent Comparison", report)
            bench_report = (summary.output_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("comparison", bench_report)
            self.assertIn("evaluation_report.md", bench_report)

    def test_direct_baseline_populates_variant_comparison_and_report(self):
        class FakeSource:
            def load(self, request):
                return [BenchCase("local__case-1", "local/repo", "abc123", "Fix local issue")]

        class FakeExecutor:
            def run(self, case, *, case_dir, agent_mode, request):
                case_dir.mkdir(parents=True, exist_ok=True)
                patch_path = case_dir / "patch.diff"
                trace_path = case_dir / "trace.json"
                patch_path.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
                trace_path.write_text(json.dumps({"events": []}), encoding="utf-8")
                return BenchCaseResult(
                    instance_id=case.instance_id,
                    repo=case.repo,
                    workspace=case_dir / "workspace",
                    trace_path=trace_path,
                    usage_report_path=None,
                    patch_path=patch_path,
                    status="patch_generated",
                    final_answer="agent done",
                    patch_chars=patch_path.stat().st_size,
                )

        class FakeBaseline:
            def predict(self, case, request):
                return {
                    "instance_id": "local__case-1",
                    "model_name_or_path": "direct-test",
                    "model_patch": "",
                    "error": "baseline config missing",
                }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agent_forge.bench.wiring.SwebenchCaseSource", return_value=FakeSource()), patch(
                "agent_forge.bench.wiring.LocalCaseExecutor", return_value=FakeExecutor()
            ), patch(
                "agent_forge.bench.wiring.DirectModelBaseline",
                return_value=FakeBaseline(),
            ):
                summary = run_swebench(
                    SwebenchRunRequest(
                        agent_mode="single",
                        output_root=tmp,
                        provider="deepseek",
                        direct_baseline=True,
                    )
                )
            self.assertIn("local__case-1", summary.variant_comparisons)
            comparison = summary.variant_comparisons["local__case-1"]
            self.assertFalse(comparison["variants"]["direct_baseline"]["patch_generated"])
            self.assertTrue(comparison["variants"]["agent_runtime"]["patch_generated"])
            self.assertNotIn("governed_agent", comparison["variants"])
            report = (summary.output_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Baseline Comparison", report)
            self.assertIn("local__case-1", report)
            self.assertNotIn("governed_agent", report)

    def test_official_eval_process_failure_is_not_patch_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.json"
            patch_path = root / "patch.diff"
            trace.write_text("{}", encoding="utf-8")
            patch_path.write_text("diff", encoding="utf-8")
            case = BenchCaseResult(
                instance_id="case-1",
                repo="local/repo",
                workspace=root,
                trace_path=trace,
                usage_report_path=None,
                patch_path=patch_path,
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
            with patch("agent_forge.bench.adapters.official_evaluator.importlib.util.find_spec", return_value=True), patch(
                "agent_forge.bench.adapters.official_evaluator.subprocess.run"
            ) as run:
                run.return_value.returncode = 2
                run.return_value.stdout = ""
                run.return_value.stderr = "docker failed"
                SwebenchOfficialEvaluator().evaluate(
                    summary,
                    SwebenchRunRequest(max_workers=1, namespace_empty=False),
                )
            self.assertEqual(case.evaluation_status, "official_eval_error")
            self.assertIn("docker failed", summary.official_eval_output)

    def test_compare_mode_combined_result_uses_multi_error_not_single_error(self):
        case = BenchCase("case-1", "local/repo", "abc123", "Fix it")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            single_dir = root / "single"
            multi_dir = root / "multi"
            for directory in [single_dir, multi_dir]:
                directory.mkdir(parents=True)
                (directory / "trace.json").write_text("{}", encoding="utf-8")
                (directory / "usage.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
                (directory / "patch.diff").write_text("diff", encoding="utf-8")
            single = BenchCaseResult(
                instance_id="case-1",
                repo="local/repo",
                workspace=single_dir,
                trace_path=single_dir / "trace.json",
                usage_report_path=None,
                patch_path=single_dir / "patch.diff",
                status="blocked",
                final_answer="single failed",
                patch_chars=0,
                error="single provider failed",
            )
            multi = BenchCaseResult(
                instance_id="case-1",
                repo="local/repo",
                workspace=multi_dir,
                trace_path=multi_dir / "trace.json",
                usage_report_path=None,
                patch_path=multi_dir / "patch.diff",
                status="patch_generated",
                final_answer="multi patched",
                patch_chars=4,
                error="",
            )
            combined_patch = root / "patch.diff"
            combined_patch.write_text("diff", encoding="utf-8")
            combined = _combined_result(case, single, multi, combined_patch)
            self.assertEqual(combined.error, "")
            self.assertEqual(combined.status, "patch_generated")

    def test_ui_latest_artifact_paths_find_compare_mode_nested_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            case_dir = (
                project_dir
                / ".agent_forge"
                / "runs"
                / "swebench-local"
                / "cases"
                / "local__case-1"
                / "multi"
                / "cases"
                / "local__case-1"
            )
            (case_dir / "multi_agent").mkdir(parents=True)
            trace_path = case_dir / "trace.json"
            usage_path = case_dir / "usage.json"
            summary_path = case_dir / "multi_agent" / "multi_agent_summary.json"
            trace_path.write_text(json.dumps({"events": []}), encoding="utf-8")
            usage_path.write_text(json.dumps({"summary": {}}), encoding="utf-8")
            summary_path.write_text(json.dumps({"status": "needs_revision"}), encoding="utf-8")

            self.assertEqual(_latest_trace_path(project_dir), trace_path)
            self.assertEqual(_latest_usage_path(project_dir), usage_path)
            self.assertEqual(_latest_multi_agent_summary_path(project_dir), summary_path)


if __name__ == "__main__":
    unittest.main()
