import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from agent_forge import Harness, HarnessConfig, HarnessExtensions
from agent_forge.runtime.application.model_step_preparation import PreparedModelStep
from agent_forge.runtime.domain.conversation import AgentResponse, ToolCall
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus
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
    dropped_context: list[str] = []
    budget_breakdown: dict[str, int] = {}
    available_tools = ["read_file"]
    permission_summary = "read allowed"
    stable_chars = 13
    dynamic_chars = 13
    dynamic_max_chars = 2_000

    def __init__(self, content: str) -> None:
        self.content = content

    def render(self) -> str:
        return self.content


class _CountingTurnSystemContextAssembler:
    def __init__(self) -> None:
        self.calls = 0
        self.freeze_calls = 0
        self.requests = []

    def freeze_stable(self, request):
        self.freeze_calls += 1
        return type(
            "StableView",
            (),
            {
                "total_chars": 13,
                "max_chars": request.max_chars,
                "truncated": False,
                "dropped_context": [],
                "budget_breakdown": {"system": 13},
                "instruction_evidence": {},
                "available_tools": ["read_file"],
                "render": lambda self: "stable-prefix",
            },
        )()

    def build(self, request):
        self.calls += 1
        self.requests.append(request)
        return _TurnSystemContextView(
            f"{request.stable_system_prefix}\nmodel-step-{self.calls}"
        )


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
        names = {item.name for item in fields(PreparedModelStep)}

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
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            assembler = _CountingTurnSystemContextAssembler()
            model = _CaptureTwoTurnModel()
            result = Harness(
                model=model,
                tools=registry,
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=2,
                    skill_mode="none",
                ),
                extensions=HarnessExtensions(
                    turn_system_context_assembler=assembler,
                ),
            ).run("read target.py and report")

        self.assertIn("semantic naming verified", result.final_answer or "")
        self.assertEqual(assembler.freeze_calls, 1)
        self.assertEqual(assembler.calls, 2)
        self.assertEqual(len(model.requests), 2)
        first_messages, first_schemas = model.requests[0]
        second_messages, second_schemas = model.requests[1]
        self.assertEqual(first_messages[0].content, "stable-prefix\nmodel-step-1")
        self.assertEqual(second_messages[0].content, "stable-prefix\nmodel-step-2")
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

    def test_checkpoint_v4_points_to_thread_state_and_v3_fails_closed(self) -> None:
        checkpoint = TaskCheckpoint(
            run_id="run-v4",
            thread_id="thread-1",
            turn_id="turn-1",
            workspace="/workspace",
            execution_workspace="/workspace",
            execution_mode="local",
            status=TaskRunStatus.PAUSED.value,
            context_revision=2,
        )
        payload = checkpoint.to_dict()

        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["context_revision"], 2)
        self.assertNotIn("task", payload)
        self.assertNotIn("conversation_history_digest", payload)
        with self.assertRaisesRegex(ValueError, "migrate artifact to version 4"):
            TaskCheckpoint.from_dict(
                {
                    **payload,
                    "schema_version": 3,
                    "task": "legacy duplicate",
                    "conversation_history_digest": {"source_hash": "digest"},
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
