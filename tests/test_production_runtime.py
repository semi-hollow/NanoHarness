import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.models.gateway import ModelGateway, RetryPolicy
from agent_forge.runtime.agent_runtime import AgentRuntime
from agent_forge.runtime.agent_spec import AgentSpec
from agent_forge.runtime.llm_client import AgentResponse, MockLLMClient
from agent_forge.tools.diagnostics import DiagnosticsTool
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.workflows.task_graph import TaskGraph, TaskNode, TaskScheduler, TaskStatus
from agent_forge.production.diff_tracker import DiffTracker
from agent_forge.runtime.session import SessionStore
from agent_forge.observability.trace import TraceRecorder
from agent_forge.cli import build_registry


class FailingOnceLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(None, [], {"code": "temporary_failure"})
        return AgentResponse("ok", [])


class UsageLLM:
    def chat(self, messages, tools):
        return AgentResponse(
            "ok",
            [],
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 40,
                "prompt_cache_miss_tokens": 60,
            },
            response_id="resp-usage",
        )


class T(unittest.TestCase):
    def test_model_gateway_retries_and_records_usage(self):
        llm = FailingOnceLLM()
        gateway = ModelGateway(llm, retry_policy=RetryPolicy(max_attempts=2))
        response = gateway.chat([], [])
        self.assertEqual(response.content, "ok")
        self.assertEqual(gateway.last_usage.attempts, 2)
        self.assertIn("temporary_failure", gateway.last_usage.error_codes)

    def test_model_gateway_records_provider_usage_and_cost(self):
        gateway = ModelGateway(UsageLLM(), provider="deepseek", model="deepseek-v4-flash")
        response = gateway.chat([], [])
        self.assertEqual(response.content, "ok")
        self.assertEqual(gateway.last_usage.prompt_tokens, 100)
        self.assertEqual(gateway.last_usage.cache_hit_tokens, 40)
        self.assertEqual(gateway.last_usage.cache_miss_tokens, 60)
        self.assertEqual(gateway.last_usage.response_id, "resp-usage")
        self.assertGreater(gateway.last_usage.estimated_cost_usd, 0)

    def test_task_graph_scheduler_runs_dependencies(self):
        graph = TaskGraph()
        graph.add(TaskNode("a", "agent", "first"))
        graph.add(TaskNode("b", "agent", "second", depends_on=["a"]))
        seen = []

        class Result:
            success = True

        TaskScheduler(graph, {"agent": lambda node: seen.append(node.node_id) or Result()}).run()
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(graph.nodes["b"].status, TaskStatus.PASSED)

    def test_diagnostics_compile_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.py"
            path.write_text("def broken(:\n", encoding="utf-8")
            obs = DiagnosticsTool(WorkspaceSandbox(d)).execute({"kind": "compile", "target": "bad.py"})
            self.assertFalse(obs.success)
            self.assertIn("bad.py", obs.content)

    def test_diff_tracker_and_session_store_write_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "x.py").write_text("a = 1\n", encoding="utf-8")
            tracker = DiffTracker(root)
            tracker.capture_before()
            (root / "x.py").write_text("a = 2\n", encoding="utf-8")
            summary = tracker.summarize_after()
            self.assertIn("x.py", summary.changed_files)

            store = SessionStore(root / ".agent_forge/runs")
            session, run_dir = store.start(str(root), "single", "task")
            store.finish(session, run_dir, "done", "trace.json")
            data = json.loads((run_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "completed")

    def test_agent_runtime_uses_agent_spec(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "examples/demo_repo/src").mkdir(parents=True)
            Path(d, "examples/demo_repo/tests").mkdir(parents=True)
            Path(d, "examples/demo_repo/src/calculator.py").write_text(
                "def add(a: int, b: int) -> int:\n    return a - b\n",
                encoding="utf-8",
            )
            tr = TraceRecorder(str(Path(d) / "trace.json"))
            spec = AgentSpec(
                "PlannerAgent",
                "planner",
                "plan only",
                allowed_tools=set(),
                max_steps=2,
            )
            result = AgentRuntime(spec, d, build_registry(d, True), tr, MockLLMClient("planner")).run("fix")
            self.assertTrue(result.success)
            self.assertEqual(result.agent_name, "PlannerAgent")


if __name__ == "__main__":
    unittest.main()
