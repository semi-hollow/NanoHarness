import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.task_state import TaskRunStatus, TaskStateStore
from agent_forge.tools.registry import ToolRegistry


class CapturingLLM:
    last_usage = None

    def __init__(self):
        self.system_context = ""

    def chat(self, messages, tools):
        self.system_context = messages[0].content
        return AgentResponse("PASS\ncontinued from checkpoint", [])


class TaskResumeTest(unittest.TestCase):
    def test_resume_state_seeds_context_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_store = TaskStateStore(root / "old_state")
            old_checkpoint = old_store.start(
                "old-run",
                "fix original failure",
                str(root),
                "CodingAgent",
            )
            old_store.update(
                old_checkpoint,
                status=TaskRunStatus.BLOCKED.value,
                current_step=3,
                last_tool="apply_patch",
                last_observation="old text not found",
                stop_reason="patch_mismatch",
                resume_hint="Re-read the file and repair the patch anchor.",
            )

            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            llm = CapturingLLM()
            config = RuntimeConfig(
                workspace=str(root),
                max_steps=1,
                trace_file=str(trace_path),
                resume_state=str(old_store.path_for("old-run")),
            )

            final = AgentLoop(config, trace, ToolRegistry(), llm).run("continue the fix")

            self.assertIn("continued from checkpoint", final)
            self.assertIn("resume_from_run=old-run", llm.system_context)
            self.assertIn("last_tool=apply_patch", llm.system_context)
            self.assertTrue(
                any(event["event_type"] == "resume_state_loaded" for event in trace.events)
            )


if __name__ == "__main__":
    unittest.main()
