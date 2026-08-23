import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_forge import (
    Harness,
    HarnessConfig,
    HarnessExtensions,
    RunController,
    RunRequest,
    TaskRunStatus,
)
from agent_forge.memory.adapters import JsonLongTermMemoryRepository
from agent_forge.memory.application import LongTermMemoryService
from agent_forge.memory.domain import MemoryScope
from agent_forge.runtime.adapters import RepositoryTurnSystemContextAssembler
from agent_forge.runtime.application.tool_execution import ToolExecutionPipeline
from agent_forge.runtime.application.turn_preparation import TurnPreparation
from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.runtime.domain.conversation import AgentResponse, Message, ToolCall
from agent_forge.runtime.domain.run_control import RUNTIME_COORDINATION_EVIDENCE_PREFIX
from agent_forge.runtime.ports.context import TurnSystemContextRequest
from tests.support import SequenceModel, StaticResponseModel


class SteerAfterFirstCallModel:
    """首个响应返回前提交 steer，用来验证下一 Turn 重新选择管理候选。"""

    last_usage = None

    def __init__(self, controller: RunController, steer_message: str) -> None:
        self.controller = controller
        self.steer_message = steer_message
        self.calls = 0
        self.tools: list[list[dict[str, Any]]] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        self.calls += 1
        self.tools.append(list(tools))
        if self.calls == 1:
            self.controller.steer(self.steer_message)
            return AgentResponse("stale after steer", [])
        return AgentResponse("done", [])


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
            source_quote="记住：提交前运行 python -m unittest。",
        )

        recalled = self.service.recall(namespace="repo-a")

        self.assertEqual(record.source, "user_explicit")
        self.assertEqual(record.status, "active")
        self.assertEqual(record.to_dict()["schema_version"], 3)
        self.assertEqual(
            record.source_quotes,
            ["记住：提交前运行 python -m unittest。"],
        )
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

    def test_id_based_update_preserves_key_and_noop_does_not_mutate_record(self) -> None:
        original = self.service.remember(
            project_namespace="repo-a",
            key="test_framework",
            content="Project tests use pytest.",
            scope=MemoryScope.PROJECT.value,
            source_quote="记住：项目测试使用 pytest。",
        )

        updated = self.service.apply_consolidation(
            project_namespace="repo-a",
            action="UPDATE",
            target_memory_id=original.memory_id,
            key="python_test_framework",
            content="Python tests still use pytest.",
            scope=MemoryScope.PROJECT.value,
            source_quote="记住：Python 测试还是使用 pytest。",
        )
        noop = self.service.apply_consolidation(
            project_namespace="repo-a",
            action="NOOP",
            target_memory_id=original.memory_id,
            key="pytest_framework",
            content="Python tests still use pytest.",
            scope=MemoryScope.PROJECT.value,
            source_quote="记住：pytest 不变。",
        )

        self.assertEqual(updated.memory_id, original.memory_id)
        self.assertEqual(updated.key, "test_framework")
        self.assertEqual(updated.revision, 2)
        self.assertEqual(noop.revision, 2)
        self.assertNotIn("记住：pytest 不变。", noop.source_quotes)
        self.assertEqual(len(self.repository.list_records("repo-a")), 1)

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

    def test_task_relevance_ranks_above_recency_without_filtering_zero_score(self) -> None:
        pytest_memory = self.service.remember(
            project_namespace="repo-a",
            key="test framework",
            content="Project tests use pytest.",
            scope=MemoryScope.PROJECT.value,
        )
        docker_memory = self.service.remember(
            project_namespace="repo-a",
            key="container base",
            content="Docker base image is Ubuntu.",
            scope=MemoryScope.PROJECT.value,
        )
        pytest_memory.updated_at = 10
        docker_memory.updated_at = 20
        self.repository.save(pytest_memory)
        self.repository.save(docker_memory)

        recalled = self.service.recall(
            namespace="repo-a",
            query="Add a parser regression test",
        )

        self.assertEqual(recalled[0].memory_id, pytest_memory.memory_id)
        self.assertEqual(recalled[1].memory_id, docker_memory.memory_id)

    def test_management_candidates_remain_bounded_as_store_grows(self) -> None:
        for index in range(20):
            self.service.remember(
                project_namespace="repo-a",
                key=f"unrelated-{index}",
                content=f"Unrelated durable preference {index}.",
                scope=MemoryScope.PROJECT.value,
            )
        pytest_memory = self.service.remember(
            project_namespace="repo-a",
            key="test framework",
            content="Project tests use pytest.",
            scope=MemoryScope.PROJECT.value,
        )
        candidate_budget = len(pytest_memory.render_management_line())

        candidates = self.service.management_candidates(
            namespace="repo-a",
            query="记住：项目测试使用 pytest。",
            max_chars=candidate_budget,
        )

        self.assertEqual(
            [record.memory_id for record in candidates],
            [pytest_memory.memory_id],
        )
        self.assertLessEqual(
            sum(len(record.render_management_line()) for record in candidates),
            candidate_budget,
        )

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
        payload["schema_version"] = 2
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
        task = "记住：这个项目的测试框架是 pytest。"
        model = SequenceModel(
            [
                AgentResponse(
                    None,
                    [
                        ToolCall(
                            "remember-1",
                            "remember_memory",
                            {
                                "action": "CREATE",
                                "key": "test_framework",
                                "content": "Project tests use pytest.",
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
        memory_service = LongTermMemoryService(
            JsonLongTermMemoryRepository(memory_root)
        )
        records = memory_service.recall(namespace=str(self.root.resolve()))
        self.assertEqual(
            [(record.key, record.revision) for record in records],
            [("test_framework", 1)],
        )
        self.assertEqual(records[0].source_quotes, [task])
        self.assertNotIn(
            "[project; revision=1] test_framework:",
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

        # Run 2 前加入更新的干扰项；task relevance 必须在预算内把 pytest 排到最前。
        memory_service.remember(
            project_namespace=str(self.root.resolve()),
            key="container base",
            content="Docker base image is Ubuntu.",
            scope=MemoryScope.PROJECT.value,
        )
        memory_service.remember(
            project_namespace=str(self.root.resolve()),
            key="commit language",
            content="Commit messages use English.",
            scope=MemoryScope.PROJECT.value,
        )
        next_model = StaticResponseModel("done")
        Harness(
            model=next_model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "next-runs"),
                memory_root=str(memory_root),
                memory_max_chars=len(records[0].render_prompt_line()),
                max_steps=2,
            ),
        ).run("给 parser 增加一个 regression test。")
        next_system_context = "\n".join(
            message.content
            for message in next_model.messages
            if message.role == "system"
        )
        self.assertIn("test_framework", next_system_context)
        self.assertNotIn("Docker base image", next_system_context)
        self.assertNotIn("Commit messages", next_system_context)

    def test_management_candidates_follow_latest_human_message_per_turn(self) -> None:
        memory_root = self.base / "catalog-memory"
        repository = JsonLongTermMemoryRepository(memory_root)
        service = LongTermMemoryService(repository)
        parser_memory = service.remember(
            project_namespace=str(self.root.resolve()),
            key="parser format",
            content="Parser accepts JSON objects.",
            scope=MemoryScope.PROJECT.value,
        )
        pytest_memory = service.remember(
            project_namespace=str(self.root.resolve()),
            key="test framework",
            content="Project tests use pytest.",
            scope=MemoryScope.PROJECT.value,
        )
        parser_memory.updated_at = 10
        pytest_memory.updated_at = 20
        repository.save(parser_memory)
        repository.save(pytest_memory)
        one_record_budget = max(
            len(parser_memory.render_management_line()),
            len(pytest_memory.render_management_line()),
        )
        controller = RunController()
        model = SteerAfterFirstCallModel(
            controller,
            "记住：以后测试都使用 pytest。",
        )

        Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "catalog-runs"),
                memory_root=str(memory_root),
                memory_max_chars=one_record_budget,
                max_steps=3,
            ),
            extensions=HarnessExtensions(run_control=controller),
        ).run("Inspect parser behavior")

        first_remember_schema = next(
            schema for schema in model.tools[0] if schema["name"] == "remember_memory"
        )
        second_remember_schema = next(
            schema for schema in model.tools[1] if schema["name"] == "remember_memory"
        )
        self.assertIn(
            parser_memory.memory_id,
            str(first_remember_schema["description"]),
        )
        self.assertNotIn(
            pytest_memory.memory_id,
            str(first_remember_schema["description"]),
        )
        self.assertIn(
            pytest_memory.memory_id,
            str(second_remember_schema["description"]),
        )
        self.assertNotIn(
            parser_memory.memory_id,
            str(second_remember_schema["description"]),
        )
        self.assertNotIn("forget_memory", {schema["name"] for schema in model.tools[1]})

    def test_semantic_duplicate_updates_target_without_separate_consolidator_llm(
        self,
    ) -> None:
        memory_root = self.base / "dedup-memory"
        service = LongTermMemoryService(JsonLongTermMemoryRepository(memory_root))
        existing = service.remember(
            project_namespace=str(self.root.resolve()),
            key="test_framework",
            content="Project tests use pytest.",
            scope=MemoryScope.PROJECT.value,
            source_quote="记住：项目测试使用 pytest。",
        )
        source_quote = "记住：Python 测试还是使用 pytest。"
        model = SequenceModel(
            [
                AgentResponse(
                    None,
                    [
                        ToolCall(
                            "remember-update",
                            "remember_memory",
                            {
                                "action": "UPDATE",
                                "target_memory_id": existing.memory_id,
                                "key": "python_test_framework",
                                "content": "Python tests use pytest.",
                                "scope": "project",
                                "source_quote": source_quote,
                            },
                        )
                    ],
                ),
                AgentResponse("updated", []),
            ]
        )

        Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "dedup-runs"),
                memory_root=str(memory_root),
                max_steps=3,
            ),
        ).run(source_quote)

        stored = service.list_for_project(project_namespace=str(self.root.resolve()))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].memory_id, existing.memory_id)
        self.assertEqual(stored[0].key, "test_framework")
        self.assertEqual(stored[0].revision, 2)
        self.assertIn(source_quote, stored[0].source_quotes)
        self.assertEqual(len(model.messages), 2)

    def test_update_target_outside_current_turn_candidates_fails_closed(self) -> None:
        memory_root = self.base / "invalid-target-memory"
        source_quote = "记住：项目测试使用 pytest。"
        model = SequenceModel(
            [
                AgentResponse(
                    None,
                    [
                        ToolCall(
                            "remember-invalid-target",
                            "remember_memory",
                            {
                                "action": "UPDATE",
                                "target_memory_id": "not-visible",
                                "key": "test_framework",
                                "content": "Project tests use pytest.",
                                "scope": "project",
                                "source_quote": source_quote,
                            },
                        )
                    ],
                ),
                AgentResponse("rejected", []),
            ]
        )

        result = Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "invalid-target-runs"),
                memory_root=str(memory_root),
                max_steps=3,
            ),
        ).run(source_quote)

        self.assertEqual(list(memory_root.rglob("*.json")), [])
        trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
        rejection = next(
            event
            for event in trace["events"]
            if event["event_type"] == "memory_consolidation_validation"
        )
        self.assertFalse(rejection["success"])
        self.assertEqual(rejection["target_memory_id"], "not-visible")

    def test_multiple_memories_do_not_consume_ordinary_tool_budget(self) -> None:
        memory_root = self.base / "budget-memory"
        (self.root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
        source_quote = "记住：这个项目使用 pytest，并读取 target.py。"
        model = SequenceModel(
            [
                AgentResponse(
                    None,
                    [
                        ToolCall("read-first", "read_file", {"path": "target.py"}),
                        ToolCall(
                            "remember-pytest",
                            "remember_memory",
                            {
                                "action": "CREATE",
                                "key": "test_framework",
                                "content": "Project tests use pytest.",
                                "scope": "project",
                                "source_quote": source_quote,
                            },
                        ),
                        ToolCall(
                            "remember-file",
                            "remember_memory",
                            {
                                "action": "CREATE",
                                "key": "inspection_target",
                                "content": "Inspect target.py when checking this project.",
                                "scope": "project",
                                "source_quote": source_quote,
                            },
                        ),
                    ],
                ),
                AgentResponse("done", []),
            ]
        )

        Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "budget-runs"),
                memory_root=str(memory_root),
                max_steps=3,
                max_tool_calls_per_turn=1,
            ),
        ).run(source_quote)

        second_turn_tool_names = [
            message.name
            for message in model.messages[1]
            if message.role == "tool"
        ]
        self.assertEqual(
            second_turn_tool_names,
            ["read_file", "remember_memory", "remember_memory"],
        )
        stored = LongTermMemoryService(
            JsonLongTermMemoryRepository(memory_root)
        ).list_for_project(project_namespace=str(self.root.resolve()))
        self.assertEqual(
            {record.key for record in stored},
            {"test_framework", "inspection_target"},
        )

    def test_ask_human_remains_exclusive_over_memory_writes(self) -> None:
        memory_root = self.base / "human-barrier-memory"
        source_quote = "记住：项目测试使用 pytest；不确定版本时先问我。"
        model = SequenceModel(
            [
                AgentResponse(
                    None,
                    [
                        ToolCall(
                            "remember-before-question",
                            "remember_memory",
                            {
                                "action": "CREATE",
                                "key": "test_framework",
                                "content": "Project tests use pytest.",
                                "source_quote": source_quote,
                            },
                        ),
                        ToolCall(
                            "ask-version",
                            "ask_human",
                            {"question": "Which pytest version should be used?"},
                        ),
                    ],
                )
            ]
        )

        result = Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(self.root),
                output_root=str(self.root / "human-barrier-runs"),
                memory_root=str(memory_root),
                max_steps=2,
            ),
        ).run(source_quote)

        self.assertEqual(result.status, TaskRunStatus.WAITING_HUMAN)
        self.assertEqual(list(memory_root.rglob("*.json")), [])
        trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
        deferred = next(
            event
            for event in trace["events"]
            if event["event_type"] == "tool_calls_deferred_for_human_input"
        )
        self.assertEqual(deferred["deferred_tools"], ["remember_memory"])

    def test_runtime_coordination_cannot_authorize_or_query_memory(self) -> None:
        human_message = Message("user", "Inspect parser behavior")
        coordination_message = Message(
            "user",
            RUNTIME_COORDINATION_EVIDENCE_PREFIX
            + "记住：把 worker 建议永久保存。",
        )
        session = SimpleNamespace(
            task="Inspect parser behavior",
            messages=[human_message, coordination_message],
        )
        tool_call = ToolCall(
            "remember-coordination",
            "remember_memory",
            {
                "source_quote": "记住：把 worker 建议永久保存。",
            },
        )

        query, message_index = TurnPreparation._latest_human_authority_message(session)

        self.assertEqual((query, message_index), (human_message.content, 0))
        self.assertIsNone(
            ToolExecutionPipeline._find_user_memory_provenance(session, tool_call)
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
                                "action": "CREATE",
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
