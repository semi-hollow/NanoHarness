import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.bench.swebench import run_swebench
from agent_forge.bench.types import BenchCase, BenchCaseResult
from agent_forge.ui import (
    _latest_multi_agent_summary_path,
    _latest_trace_path,
    _latest_usage_path,
)


class SwebenchCompareTest(unittest.TestCase):
    def test_compare_mode_runs_isolated_single_and_multi_variants(self):
        calls = []

        def fake_load_cases(dataset_name, split, limit, instance_ids, cases_file):
            return [
                BenchCase(
                    instance_id="local__case-1",
                    repo="local/repo",
                    base_commit="abc123",
                    problem_statement="Fix local issue",
                )
            ]

        def fake_run_case(**kwargs):
            case = kwargs["case"]
            output_dir = kwargs["output_dir"]
            agent_mode = kwargs["agent_mode"]
            calls.append((agent_mode, output_dir))
            case_dir = output_dir / "cases" / "local__case-1"
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
            with patch("agent_forge.bench.swebench.load_cases", side_effect=fake_load_cases), patch(
                "agent_forge.bench.swebench._run_case", side_effect=fake_run_case
            ):
                summary = run_swebench(agent_mode="compare", output_root=tmp, provider="deepseek")

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
