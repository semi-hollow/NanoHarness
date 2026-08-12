import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.application.swebench import _new_summary
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.presentation.report import render_bench_report
from agent_forge.cli.parser import build_parser
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry


class ToolExecutionTimeoutTest(unittest.TestCase):
    def test_benchmark_cli_and_request_accept_formal_profile_timeout(self):
        parser = build_parser()
        swebench_args = parser.parse_args(
            [
                "bench",
                "swebench",
                "--tool-execution-timeout-seconds",
                "600",
            ]
        )
        campaign_args = parser.parse_args(
            [
                "bench",
                "campaign",
                "--tool-execution-timeout-seconds",
                "600",
            ]
        )

        self.assertEqual(swebench_args.tool_execution_timeout_seconds, 600)
        self.assertEqual(campaign_args.tool_execution_timeout_seconds, 600)
        self.assertEqual(
            SwebenchRunRequest(
                tool_execution_timeout_seconds=600
            ).tool_execution_timeout_seconds,
            600,
        )
        self.assertEqual(
            RuntimeConfig(
                workspace=".", tool_execution_timeout_seconds=600
            ).tool_execution_timeout_seconds,
            600,
        )

    def test_request_and_registry_reject_non_positive_timeout(self):
        with self.assertRaisesRegex(ValueError, "must be positive"):
            SwebenchRunRequest(tool_execution_timeout_seconds=0)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            ToolRegistryBuildRequest(
                workspace=".", auto=True, tool_execution_timeout_seconds=0
            )

    def test_registry_applies_one_timeout_to_both_validation_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_registry(
                ToolRegistryBuildRequest(
                    workspace=tmp,
                    auto=True,
                    tool_execution_timeout_seconds=600,
                )
            )

        self.assertEqual(registry.get("run_command").timeout_seconds, 600)
        self.assertEqual(registry.get("python_validation").timeout_seconds, 600)

    def test_summary_records_timeout_as_run_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = _new_summary(
                SwebenchRunRequest(tool_execution_timeout_seconds=600),
                "run-timeout",
                BenchRunLayout(
                    output_dir=root,
                    predictions_path=root / "predictions.jsonl",
                ),
            )

        self.assertEqual(summary.tool_execution_timeout_seconds, 600)
        self.assertEqual(summary.to_dict()["tool_execution_timeout_seconds"], 600)
        self.assertIn("tool execution timeout: `600s`", render_bench_report(summary))


if __name__ == "__main__":
    unittest.main()
