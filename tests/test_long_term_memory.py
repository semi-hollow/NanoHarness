import tempfile
import unittest
from pathlib import Path

from agent_forge.context.adapters import JsonLongTermMemoryRepository
from agent_forge.context.application import LongTermMemoryService
from agent_forge.context.domain import MemoryScope
from agent_forge.runtime.adapters import RepositoryContextAssembler
from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.runtime.ports.context import ContextAssemblyRequest


class LongTermMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # 真实运行把状态放在隐藏目录；不能让 Context 检索把记忆 JSON 当源码读取。
        self.repository = JsonLongTermMemoryRepository(
            self.root / ".agent_forge/internal/state/memory"
        )
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

        self.assertEqual([item.memory_id for item in repo_a_memories], [project_value.memory_id])
        self.assertEqual([item.memory_id for item in repo_b_memories], [user_default.memory_id])

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

        report = RepositoryContextAssembler().build(
            ContextAssemblyRequest(
                task="inspect parser JSON behavior",
                workspace=str(self.root),
                working_memory=memory,
                tools=[],
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


if __name__ == "__main__":
    unittest.main()
