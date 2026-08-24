import ast
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from agent_forge import Harness, HarnessConfig, HarnessExtensions, RunRequest
from agent_forge.runtime.adapters import JsonHumanInputRepository
from agent_forge.runtime.application.model_step_preparation import PreparedModelStep
from agent_forge.runtime.domain.conversation import AgentResponse, ToolCall
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.ask_human import AskHumanTool
from agent_forge.tools.builtins.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry
from scripts.migrate_context_semantic_naming_v3 import (
    _semantic_normalize,
    _transform_checkpoints,
)


class _ModelStepSystemContextView:
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


class _CountingSystemContextAssembler:
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

    def build_model_step(self, request):
        self.calls += 1
        self.requests.append(request)
        return _ModelStepSystemContextView(
            f"{request.stable_system_prefix}\nmodel-step-{self.calls}"
        )


class _CaptureTwoModelStepModel:
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


class _TwoModelStepsThenHumanModel:
    """先完成一次真实 Tool round，再把同一 Turn 停在人工输入边界。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, llm_messages, tool_schemas):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [ToolCall("read-before-pause", "read_file", {"path": "target.py"})],
            )
        return AgentResponse(
            None,
            [
                ToolCall(
                    "ask-before-resume",
                    "ask_human",
                    {"question": "Continue the same Turn?"},
                )
            ],
        )


class _ImmediateFinalModel:
    last_usage = None

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = 0

    def chat(self, llm_messages, tool_schemas):
        self.calls += 1
        return AgentResponse(self.answer, [])


class SemanticNamingTest(unittest.TestCase):
    def test_dynamic_context_uses_only_model_step_terminology(self) -> None:
        """当前源码和文档不得重新引入历史的 Turn=model iteration 名称。"""

        repository = Path(__file__).resolve().parents[1]
        forbidden = (
            "Turn" + "SystemContext",
            "turn" + "_system_context",
            "turn" + "_system_message",
            "RepositoryTurn" + "SystemContextAssembler",
            "Turn" + "SystemContextAssemblerPort",
        )
        sources = [repository / "README.md"]
        for relative_root in ("agent_forge", "apps", "docs"):
            root = repository / relative_root
            sources.extend(root.rglob("*.py"))
            sources.extend(root.rglob("*.md"))

        violations = {
            str(path.relative_to(repository)): term
            for path in sources
            for term in forbidden
            if term in path.read_text(encoding="utf-8")
        }
        self.assertEqual(violations, {})

        forbidden_identifiers = {
            "remaining_tool_" + "turns",
            "is_closeout_" + "turn",
            "tool_is_routed_for_this_" + "turn",
            "is_key_" + "turn",
            "_classify_" + "turn",
            "_feedback_for_next_" + "turn",
        }
        identifier_violations: dict[str, set[str]] = {}
        for path in [item for item in sources if item.suffix == ".py"]:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            identifiers: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    identifiers.add(node.id)
                elif isinstance(node, ast.Attribute):
                    identifiers.add(node.attr)
                elif isinstance(node, ast.arg):
                    identifiers.add(node.arg)
                elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    identifiers.add(node.name)
                elif isinstance(node, ast.keyword) and node.arg:
                    identifiers.add(node.arg)
            stale = identifiers & forbidden_identifiers
            if stale:
                identifier_violations[str(path.relative_to(repository))] = stale
        self.assertEqual(identifier_violations, {})

    def test_prepared_model_step_exposes_only_current_semantic_names(self) -> None:
        names = {item.name for item in fields(PreparedModelStep)}

        self.assertTrue(
            {
                "model_step_system_message",
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
            assembler = _CountingSystemContextAssembler()
            model = _CaptureTwoModelStepModel()
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
                    system_context_assembler=assembler,
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

    def test_context_lifecycle_freezes_per_turn_and_builds_per_model_step(self) -> None:
        """同 Turn resume 只重建动态输入；新 Turn 才重新冻结稳定契约。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            registry.register(AskHumanTool())
            assembler = _CountingSystemContextAssembler()
            config = HarnessConfig(
                workspace=str(root),
                output_root=str(root / "runs"),
                conversation_thread_root=str(root / "threads"),
                human_input_root=str(root / "human-input"),
                max_steps=3,
                skill_mode="none",
                tool_routing_mode="all",
            )
            extensions = HarnessExtensions(system_context_assembler=assembler)

            # Run 1 / Model Step 1-2：先执行 read_file，再停在 ask_human。
            first = Harness(
                model=_TwoModelStepsThenHumanModel(),
                tools=registry,
                config=config,
                extensions=extensions,
            ).run("read target.py, then ask whether to continue")
            self.assertEqual(first.status, TaskRunStatus.WAITING_HUMAN)
            self.assertEqual(assembler.freeze_calls, 1)
            self.assertEqual(assembler.calls, 2)

            # Run 2 / Model Step 3：人工回答后恢复同一 Turn，不重新冻结稳定输入。
            human_inputs = JsonHumanInputRepository(root / "human-input")
            pending = human_inputs.list_pending()[0]
            human_inputs.respond(pending.request_id, "continue")
            resumed_model = _ImmediateFinalModel("PASS\nresumed same Turn")
            resumed = Harness(
                model=resumed_model,
                tools=registry,
                config=config,
                extensions=extensions,
            ).resume(first.artifact_dir / "task_state" / f"{first.run_id}.json")
            self.assertEqual(resumed.thread_id, first.thread_id)
            self.assertEqual(resumed.turn_id, first.turn_id)
            self.assertNotEqual(resumed.run_id, first.run_id)
            self.assertEqual(assembler.freeze_calls, 1)
            self.assertEqual(assembler.calls, 3)
            self.assertEqual(resumed_model.calls, 1)

            # 新顶层请求创建 Turn 2；它冻结新 Snapshot，再构建自己的 Model Step。
            follow_up_model = _ImmediateFinalModel("PASS\nnew Turn")
            follow_up = Harness(
                model=follow_up_model,
                tools=registry,
                config=config,
                extensions=extensions,
            ).run(
                RunRequest(
                    "summarize the previous result",
                    thread_id=first.thread_id,
                )
            )
            self.assertEqual(follow_up.thread_id, first.thread_id)
            self.assertNotEqual(follow_up.turn_id, first.turn_id)
            self.assertEqual(assembler.freeze_calls, 2)
            self.assertEqual(assembler.calls, 4)
            self.assertEqual(follow_up_model.calls, 1)

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
