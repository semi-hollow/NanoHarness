import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.application.turn_preparation import PreparedTurn
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse, ToolCall
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus
from agent_forge.runtime.wiring import (
    AgentLoopBuildRequest,
    RuntimeDependencyOverrides,
    build_agent_loop_from_request,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry
from scripts.migrate_context_semantic_naming_v3 import (
    _semantic_normalize,
    _transform_checkpoints,
)


class _TurnSystemContextView:
    selected_files: list[str] = []
    retrieved_docs: list[str] = []
    working_memory_summary = ""
    total_chars = 13
    max_chars = 2_000
    truncated = False
    topic_relation = "same"
    inherit_session = True
    dropped_context: list[str] = []
    budget_breakdown: dict[str, int] = {}
    available_tools = ["read_file"]
    permission_summary = "read allowed"
    instruction_evidence: dict[str, object] = {}

    def __init__(self, content: str) -> None:
        self.content = content

    def render(self) -> str:
        return self.content


class _CountingTurnSystemContextAssembler:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def build(self, request):
        self.calls += 1
        self.requests.append(request)
        return _TurnSystemContextView(f"turn-system-{self.calls}")


class _CaptureTwoTurnModel:
    last_usage = None

    def __init__(self) -> None:
        self.requests = []

    def chat(self, llm_messages, tool_schemas):
        self.requests.append((list(llm_messages), list(tool_schemas)))
        if len(self.requests) == 1:
            return AgentResponse(
                None,
                [ToolCall("read-1", "read_file", {"path": "target.py"})],
            )
        return AgentResponse("PASS\nsemantic naming verified", [])


class SemanticNamingTest(unittest.TestCase):
    def test_prepared_turn_exposes_only_current_semantic_names(self) -> None:
        names = {item.name for item in fields(PreparedTurn)}

        self.assertTrue(
            {
                "turn_system_message",
                "llm_messages",
                "tool_schemas",
                "conversation_history_digest",
            }
            <= names
        )
        self.assertTrue(
            {
                "context_message",
                "messages_for_llm",
                "schemas",
                "session_digest",
            }.isdisjoint(names)
        )

    def test_model_boundary_and_cross_turn_history_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
            trace = TraceRecorder(str(root / "trace.json"))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            assembler = _CountingTurnSystemContextAssembler()
            model = _CaptureTwoTurnModel()
            config = RuntimeConfig(
                workspace=str(root),
                max_steps=2,
                trace_file=str(root / "trace.json"),
                task_state_root=str(root / "task_state"),
            )

            final = build_agent_loop_from_request(
                AgentLoopBuildRequest(
                    config=config,
                    trace=trace,
                    registry=registry,
                    llm=model,
                    overrides=RuntimeDependencyOverrides(
                        turn_system_context_assembler=assembler,
                    ),
                )
            ).run("read target.py and report")

        self.assertIn("semantic naming verified", final)
        self.assertEqual(assembler.calls, 2)
        self.assertEqual(len(model.requests), 2)
        first_messages, first_schemas = model.requests[0]
        second_messages, second_schemas = model.requests[1]
        self.assertEqual(first_messages[0].content, "turn-system-1")
        self.assertEqual(second_messages[0].content, "turn-system-2")
        self.assertIsNot(first_messages[0], second_messages[0])
        self.assertEqual(
            [message.role for message in second_messages[1:4]],
            ["user", "assistant", "tool"],
        )
        self.assertEqual(
            {schema["name"] for schema in first_schemas},
            {"read_file"},
        )
        self.assertEqual(second_schemas, [])
        self.assertEqual(assembler.requests[0].tool_schemas, first_schemas)
        self.assertEqual(assembler.requests[1].tool_schemas, second_schemas)

    def test_checkpoint_v3_is_canonical_and_v2_fails_closed(self) -> None:
        checkpoint = TaskCheckpoint(
            run_id="run-v3",
            task="continue safely",
            workspace="/workspace",
            status=TaskRunStatus.PAUSED.value,
            conversation_history_digest={"source_hash": "digest"},
        )
        payload = checkpoint.to_dict()

        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(
            payload["conversation_history_digest"],
            {"source_hash": "digest"},
        )
        self.assertNotIn("session_digest", payload)
        with self.assertRaisesRegex(ValueError, "migrate artifact to version 3"):
            TaskCheckpoint.from_dict(
                {
                    **payload,
                    "schema_version": 2,
                    "session_digest": payload["conversation_history_digest"],
                }
            )

    def test_one_time_transform_changes_only_checkpoint_contract(self) -> None:
        payload = {
            "schema_version": 2,
            "events": [
                {
                    "event_type": "task_state_checkpoint",
                    "task_state": {
                        "schema_version": 2,
                        "run_id": "run-v2",
                        "task": "repair",
                        "workspace": "/workspace",
                        "status": "paused",
                        "current_step": 2,
                        "messages_count": 3,
                        "observations_count": 1,
                        "session_digest": {"source_hash": "abc"},
                        "updated_at": 123.0,
                    },
                }
            ],
        }

        migrated, count = _transform_checkpoints(payload)

        self.assertEqual(count, 1)
        task_state = migrated["events"][0]["task_state"]
        self.assertEqual(task_state["schema_version"], 3)
        self.assertNotIn("session_digest", task_state)
        self.assertEqual(
            task_state["conversation_history_digest"],
            {"source_hash": "abc"},
        )
        self.assertEqual(_semantic_normalize(payload), _semantic_normalize(migrated))


if __name__ == "__main__":
    unittest.main()
