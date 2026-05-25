import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.usage_report import build_usage_report, write_usage_artifacts


class TestUsageReport(unittest.TestCase):
    def sample_trace(self):
        return {
            "run_id": "run-1",
            "task": "fix provider protocol",
            "stop_reason": "final_answer",
            "final_answer": "done",
            "events": [
                {
                    "run_id": "run-1",
                    "step": 1,
                    "agent_name": "CodingAgent",
                    "event_type": "context_assembly",
                    "context": {
                        "total_chars": 1000,
                        "max_chars": 2000,
                        "truncated": False,
                        "budget_breakdown": {"memory": 100, "retrieved_docs": 300},
                        "selected_files": ["a.py"],
                        "retrieved_docs_count": 1,
                    },
                },
                {
                    "run_id": "run-1",
                    "step": 1,
                    "agent_name": "CodingAgent",
                    "event_type": "llm_call",
                    "llm_input_breakdown_chars": {
                        "system_context": 1000,
                        "conversation_history": 200,
                        "tool_schemas": 400,
                    },
                    "model_usage": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "usage_source": "provider",
                        "prompt_tokens": 120,
                        "completion_tokens": 30,
                        "total_tokens": 150,
                        "cache_hit_tokens": 80,
                        "cache_miss_tokens": 40,
                        "estimated_cost_usd": 0.00002,
                        "latency_ms": 500,
                        "response_id": "resp-1",
                    },
                },
                {
                    "run_id": "run-1",
                    "step": 1,
                    "agent_name": "CodingAgent",
                    "event_type": "action",
                    "tool_call": "read_file",
                    "tool_arguments": {"path": "a.py"},
                },
                {
                    "run_id": "run-1",
                    "step": 1,
                    "agent_name": "CodingAgent",
                    "event_type": "tool_observation",
                    "success": True,
                    "observation": "file content",
                    "duration_ms": 5,
                },
            ],
        }

    def test_build_usage_report_has_step_context_and_tool_stats(self):
        usage = build_usage_report(self.sample_trace())
        self.assertEqual(usage["summary"]["llm_calls"], 1)
        self.assertEqual(usage["summary"]["prompt_tokens"], 120)
        self.assertEqual(usage["summary"]["cache_hit_tokens"], 80)
        self.assertEqual(usage["steps"][0]["llm_calls"][0]["provider_response_id"], "resp-1")
        self.assertEqual(usage["tool_efficiency"]["by_tool"]["read_file"]["calls"], 1)
        self.assertIn("tool_schemas", usage["context_breakdown"]["section_chars"])

    def test_write_usage_artifacts_for_ad_hoc_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace-demo.json"
            trace.write_text(json.dumps(self.sample_trace()), encoding="utf-8")
            usage_json, usage_md = write_usage_artifacts(trace)
            self.assertEqual(usage_json.name, "trace-demo.usage.json")
            self.assertEqual(usage_md.name, "trace-demo.usage_report.md")
            self.assertIn("Step Breakdown", usage_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
