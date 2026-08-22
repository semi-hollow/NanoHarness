import json
import tempfile
import unittest
from pathlib import Path

from agent_forge import Harness, HarnessConfig, RunRequest, TaskRunStatus
from agent_forge.memory.adapters import JsonLongTermMemoryRepository
from agent_forge.memory.application import LongTermMemoryService
from agent_forge.memory.domain import MemoryScope
from agent_forge.runtime.adapters import RepositoryTurnSystemContextAssembler
from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.runtime.domain.conversation import AgentResponse, ToolCall
from agent_forge.runtime.ports.context import TurnSystemContextRequest
from tests.support import SequenceModel, StaticResponseModel


class LongTermMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        # 真实运行把状态放在隐藏目录；不能让 Context 检索把记忆 JSON 当源码读取。
        self.repository = JsonLongTermMemoryRepository(self.base / "memory")
        self.service = LongTermMemoryService(self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_explicit_remember_is_immediately_active(self) -> None:
        record = self.service.remember(
            project_namespace="repo-a",
            key="test command",
            content="Run python -m unittest before completion.",
            scope=MemoryScope.PROJECT.value,
        )

        recalled = self.service.recall(namespace="repo-a")

        self.assertEqual(record.source, "user_explicit")
        self.assertEqual(record.status, "active")
        self.assertEqual([item.memory_id for item in recalled], [record.memory_id])

    def test_same_scope_and_key_updates_in_place(self) -> None:
        original = self.service.remember(
            project_namespace="repo-a",
            key="validation command",
            content="Run the focused test.",
            scope=MemoryScope.PROJECT.value,
        )

        updated = self.service.remember(
            project_namespace="repo-a",
            key="Validation Command",
            content="Run focused and full tests.",
            scope=MemoryScope.PROJECT.value,
        )

        self.assertEqual(updated.memory_id, original.memory_id)
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.content, "Run focused and full tests.")

    def test_project_value_overrides_user_default_with_same_key(self) -> None:
        user_default = self.service.remember(
            project_namespace="repo-a",
            key="response language",
            content="Use English.",
            scope=MemoryScope.USER.value,
        )
        project_value = self.service.remember(
            project_namespace="repo-a",
            key="response language",
            content="Use Chinese comments in this project.",
            scope=MemoryScope.PROJECT.value,
        )

        repo_a_memories = self.service.recall(namespace="repo-a")
        repo_b_memories = self.service.recall(namespace="repo-b")

        self.assertEqual(
            [item.memory_id for item in repo_a_memories], [project_value.memory_id]
        )
        self.assertEqual(
            [item.memory_id for item in repo_b_memories], [user_default.memory_id]
        )

    def test_recall_uses_character_budget_and_never_truncates_a_record(self) -> None:
        oversized = self.service.remember(
            project_namespace="repo-a",
            key="large",
            content="x" * 400,
            scope=MemoryScope.PROJECT.value,
        )
        compact = self.service.remember(
            project_namespace="repo-a",
            key="small",
            content="use pytest",
            scope=MemoryScope.PROJECT.value,
        )
        oversized.updated_at = 20
        compact.updated_at = 10
        self.repository.save(oversized)
        self.repository.save(compact)

        recalled = self.service.recall(
            namespace="repo-a",
            max_chars=len(compact.render_prompt_line()),
        )

        self.assertEqual([record.memory_id for record in recalled], [compact.memory_id])
        self.assertEqual(recalled[0].content, "use pytest")

    def test_forget_physically_removes_record(self) -> None:
        record = self.service.remember(
            project_namespace="repo-a",
            key="temporary preference",
            content="Keep explanations short.",
            scope=MemoryScope.USER.value,
        )

        deleted = self.service.forget(record.memory_id)

        self.assertEqual(deleted.memory_id, record.memory_id)
        self.assertIsNone(self.repository.get(record.memory_id))
        self.assertEqual(self.service.recall(namespace="repo-a"), [])

    def test_active_memory_root_rejects_legacy_schema(self) -> None:
        self.service.remember(
            project_namespace="repo-a",
            key="strict schema",
            content="Do not load a v1 record from the active root.",
            scope=MemoryScope.PROJECT.value,
        )
        path = next((self.base / "memory").rglob("*.json"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 1
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "unsupported memory schema_version"):
            self.service.recall(namespace="repo-a")

    def test_recalled_memory_is_rendered_as_separate_context_section(self) -> None:
        record = self.service.remember(
            project_namespace="repo-a",
            key="parser convention",
            content="The parser accepts JSON objects only.",
            scope=MemoryScope.PROJECT.value,
        )
        memory = WorkingMemory()
        memory.seed_long_term(self.service.recall(namespace="repo-a"))
        (self.root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")

        report = RepositoryTurnSystemContextAssembler().build(
            TurnSystemContextRequest(
                task="inspect parser JSON behavior",
                workspace=str(self.root),
                working_memory=memory,
                tool_schemas=[],
                active_skill_cards=[],
                max_chars=4_000,
                permission_summary="read allowed",
            )
        )

        rendered = report.render()
        self.assertEqual(len(report.long_term_memory), 1)
        self.assertIn("long_term_memory", rendered)
        self.assertIn("parser convention", rendered)
        self.assertIn("revision=1", rendered)
        self.assertNotIn(record.memory_id, rendered)

    def test_model_memory_write_requires_user_quote_and_uses_operation_ledger(
        self,
    ) -> None:
        memory_root = self.base / "tool-memory"
        task = "记住，这个项目以后统一使用 python -m pytest。"
        model = SequenceModel(
            [
                AgentResponse(
                    None,
                    [
                        ToolCall(
                            "remember-1",
                            "remember_memory",
                            {
                                "key": "test_command",
                                "content": "Use python -m pytest.",
                                "source_quote": task,
                            },
                        )
                    ],
                ),
                AgentResponse("memory saved", []),
            ]
        )
        result = Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "runs"),
                memory_root=str(memory_root),
                max_steps=3,
                auto_approve_writes=False,
            ),
        ).run(RunRequest(task))

        self.assertEqual(result.status, TaskRunStatus.COMPLETED)
        records = LongTermMemoryService(
            JsonLongTermMemoryRepository(memory_root)
        ).recall(namespace=str(self.root.resolve()))
        self.assertEqual(
            [(record.key, record.revision) for record in records], [("test_command", 1)]
        )
        self.assertNotIn(
            "[project; revision=1] test_command:",
            "\n".join(
                message.content
                for message in model.messages[1]
                if message.role == "system"
            ),
        )

        ledger_path = next(
            (self.root / ".agent_forge/internal/state/operation_ledger").glob("*.json")
        )
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(ledger["action"], "memory_write")
        self.assertEqual(ledger["arguments"]["source_quote"], task)
        trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
        provenance = next(
            event["provenance"]
            for event in trace["events"]
            if event["event_type"] == "memory_authorization" and event["success"]
        )
        self.assertEqual(provenance["message_index"], 0)
        self.assertEqual(provenance["source_quote"], task)

        next_model = StaticResponseModel("done")
        Harness(
            model=next_model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "next-runs"),
                memory_root=str(memory_root),
                max_steps=2,
            ),
        ).run("Summarize the test convention.")
        self.assertIn(
            "test_command",
            "\n".join(
                message.content
                for message in next_model.messages
                if message.role == "system"
            ),
        )

    def test_model_memory_write_fails_closed_without_matching_user_quote(self) -> None:
        memory_root = self.base / "rejected-memory"
        model = SequenceModel(
            [
                AgentResponse(
                    None,
                    [
                        ToolCall(
                            "remember-2",
                            "remember_memory",
                            {
                                "key": "invented",
                                "content": "Do not persist this.",
                                "source_quote": "the user never said this",
                            },
                        )
                    ],
                ),
                AgentResponse("write rejected", []),
            ]
        )
        result = Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "rejected-runs"),
                memory_root=str(memory_root),
                max_steps=3,
            ),
        ).run("Explain the repository; do not save preferences.")

        self.assertEqual(result.status, TaskRunStatus.COMPLETED)
        self.assertEqual(list(memory_root.rglob("*.json")), [])
        trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
        rejection = next(
            event
            for event in trace["events"]
            if event["event_type"] == "memory_authorization"
        )
        self.assertFalse(rejection["success"])


if __name__ == "__main__":
    unittest.main()
