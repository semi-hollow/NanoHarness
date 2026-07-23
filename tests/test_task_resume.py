import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import JsonTaskStateRepository
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.task import (
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry
from tests.support import StaticResponseModel


class TwoReadsThenFinalLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall("read-a", "read_file", {"path": "a.txt"}),
                    ToolCall("read-b", "read_file", {"path": "b.txt"}),
                ],
            )
        return AgentResponse("PASS\ncontinued after compaction", [])


class TaskResumeTest(unittest.TestCase):
    def test_resume_state_seeds_context_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_store = JsonTaskStateRepository(root / "old_state")
            old_checkpoint = old_store.start(
                TaskStartRequest(
                    run_id="old-run",
                    task="fix original failure",
                    workspace=str(root),
                    agent_name="CodingAgent",
                )
            )
            old_store.update(
                old_checkpoint,
                TaskCheckpointUpdate(
                    status=TaskRunStatus.BLOCKED.value,
                    current_step=3,
                    last_tool="apply_patch",
                    last_observation="old text not found",
                    stop_reason="patch_mismatch",
                    resume_hint="Re-read the file and repair the patch anchor.",
                    context_digest={
                        "schema_version": 1,
                        "source_hash": "digest-old",
                        "open_failures": ["patch anchor mismatch"],
                    },
                ),
            )

            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            llm = StaticResponseModel("PASS\ncontinued from checkpoint")
            config = RuntimeConfig(
                workspace=str(root),
                max_steps=1,
                trace_file=str(trace_path),
                resume_state=str(old_store.path_for("old-run")),
            )

            final = build_agent_loop(config, trace, ToolRegistry(), llm).run(
                "continue the fix"
            )

            self.assertIn("continued from checkpoint", final)
            system_context = llm.messages[0].content or ""
            self.assertIn("resume_from_run=old-run", system_context)
            self.assertIn("last_tool=apply_patch", system_context)
            self.assertIn("digest-old", system_context)
            self.assertTrue(
                any(
                    event["event_type"] == "resume_state_loaded"
                    for event in trace.events
                )
            )

    def test_compaction_digest_is_persisted_in_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a" * 8_000, encoding="utf-8")
            (root / "b.txt").write_text("b" * 8_000, encoding="utf-8")
            trace_path = root / "trace.json"
            state_root = root / "task_state"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(
                workspace=str(root),
                max_steps=2,
                max_context_chars=1_000,
                max_prompt_tokens=2_000,
                reserved_output_tokens=200,
                trace_file=str(trace_path),
                task_state_root=str(state_root),
            )

            final = build_agent_loop(
                config,
                trace,
                registry,
                TwoReadsThenFinalLLM(),
            ).run("read both files and summarize")

            checkpoint = JsonTaskStateRepository(state_root).list()[0]

        self.assertIn("continued after compaction", final)
        self.assertTrue(checkpoint.context_digest)
        self.assertGreater(checkpoint.context_digest["covered_message_count"], 0)
        self.assertTrue(
            any(
                event["event_type"] == "context_window"
                and event["context_window"]["compacted"]
                for event in trace.events
            )
        )


if __name__ == "__main__":
    unittest.main()
