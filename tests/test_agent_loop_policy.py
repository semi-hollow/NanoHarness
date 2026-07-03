import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.observability.trace import TraceRecorder
from agent_forge.tools.registry import ToolRegistry


class FinalAnswerLLM:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse("PASS\nfinal answer", [])


class AgentLoopPolicyTest(unittest.TestCase):
    def test_agent_name_flows_into_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(workspace=tmp, max_steps=2, trace_file=str(trace_path))
            final = AgentLoop(config, trace, ToolRegistry(), FinalAnswerLLM()).run("summarize safely", agent_name="Reviewer")
            self.assertIn("final answer", final)
            agent_names = {event["agent_name"] for event in trace.events}
            self.assertIn("Reviewer", agent_names)
            self.assertTrue(any(event["event_type"] == "final_answer" for event in trace.events))


if __name__ == "__main__":
    unittest.main()
