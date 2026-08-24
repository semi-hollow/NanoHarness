import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent_forge.context.application.compaction as compaction_module
from agent_forge.context.application.compaction import (
    PromptWindowRequest,
    PromptWindowManager,
    PromptBudget,
)
from agent_forge.runtime.domain.conversation import Message, Observation
from agent_forge.runtime.application.session import (
    load_transaction_safe_conversation_page,
)


class PromptWindowManagerTest(unittest.TestCase):
    def test_bounded_page_looks_ahead_to_keep_tool_batch_atomic(self) -> None:
        items = [
            SimpleNamespace(
                sequence=index,
                role="user",
                tool_calls=(),
                tool_call_id=None,
            )
            for index in range(1, 200)
        ]
        items.extend(
            [
                SimpleNamespace(
                    sequence=200,
                    role="assistant",
                    tool_calls=({"id": "call-a"}, {"id": "call-b"}),
                    tool_call_id=None,
                ),
                SimpleNamespace(
                    sequence=201,
                    role="tool",
                    tool_calls=(),
                    tool_call_id="call-a",
                ),
                SimpleNamespace(
                    sequence=202,
                    role="tool",
                    tool_calls=(),
                    tool_call_id="call-b",
                ),
            ]
        )

        class Repository:
            def list_items(self, thread_id, *, after_sequence=0, turn_id=None, limit=200):
                del thread_id, turn_id
                return [
                    item for item in items if item.sequence > after_sequence
                ][:limit]

        page = load_transaction_safe_conversation_page(
            Repository(),  # type: ignore[arg-type]
            thread_id="thread-1",
            after_sequence=0,
            limit=200,
        )
        self.assertEqual([item.sequence for item in page[-3:]], [200, 201, 202])

    def test_ask_human_page_projects_provider_order_and_compacts_atomically(self) -> None:
        def tool_call(call_id: str, name: str) -> dict:
            return {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }

        items = [
            SimpleNamespace(
                sequence=1,
                role="assistant",
                content="",
                name=None,
                tool_calls=(
                    tool_call("read-1", "read_file"),
                    tool_call("ask-1", "ask_human"),
                    tool_call("write-1", "write_file"),
                ),
                tool_call_id=None,
                reasoning_content=None,
                origin="model_tool_calls",
                human_authority=False,
            ),
            SimpleNamespace(
                sequence=2,
                role="tool",
                content="deferred read",
                name="read_file",
                tool_calls=(),
                tool_call_id="read-1",
                reasoning_content=None,
                origin="tool_runtime",
                human_authority=False,
            ),
            # raw authority-first journal order: answer precedes ask_human Observation.
            SimpleNamespace(
                sequence=3,
                role="user",
                content=(
                    "Operator answer to the requested question:\n"
                    "Question: Python version?\nAnswer: 3.11"
                ),
                name=None,
                tool_calls=(),
                tool_call_id=None,
                reasoning_content=None,
                origin="operator",
                human_authority=True,
                metadata={"human_input_request_id": "request-1"},
            ),
            SimpleNamespace(
                sequence=4,
                role="tool",
                content="human_response: 3.11",
                name="ask_human",
                tool_calls=(),
                tool_call_id="ask-1",
                reasoning_content=None,
                origin="tool_runtime",
                human_authority=False,
            ),
            SimpleNamespace(
                sequence=5,
                role="tool",
                content="deferred write",
                name="write_file",
                tool_calls=(),
                tool_call_id="write-1",
                reasoning_content=None,
                origin="tool_runtime",
                human_authority=False,
            ),
        ]

        class Repository:
            def list_items(self, thread_id, *, after_sequence=0, turn_id=None, limit=200):
                del thread_id, turn_id
                return [
                    item for item in items if item.sequence > after_sequence
                ][:limit]

        projected = load_transaction_safe_conversation_page(
            Repository(),  # type: ignore[arg-type]
            thread_id="thread-1",
            after_sequence=0,
            limit=3,
        )
        self.assertEqual([item.sequence for item in projected], [1, 2, 4, 5, 3])
        messages = [
            Message(
                role=item.role,
                content=item.content,
                name=item.name,
                tool_call_id=item.tool_call_id,
                tool_calls=list(item.tool_calls) or None,
                reasoning_content=item.reasoning_content,
                origin=item.origin,
                human_authority=item.human_authority,
            )
            for item in projected
        ]
        segments = compaction_module._group_history_segments(
            messages,
            [
                Observation("read_file", False, "deferred read"),
                Observation("ask_human", True, "human_response: 3.11"),
                Observation("write_file", False, "deferred write"),
            ],
        )
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(segments[0].messages), 5)

    def test_rejects_output_reserve_larger_than_model_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved_output_tokens"):
            PromptBudget(
                max_prompt_tokens=1_000,
                reserved_output_tokens=1_000,
            )

    def test_compacts_old_history_without_splitting_tool_transaction(self) -> None:
        history = [Message("user", "fix the parser and run tests")]
        observations = []
        for index in range(6):
            call_id = f"call-{index}"
            history.append(
                Message(
                    "assistant",
                    "",
                    tool_calls=[
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"target.py"}',
                            },
                        }
                    ],
                )
            )
            history.append(
                Message(
                    "tool",
                    f"result-{index} " + ("x" * 900),
                    name="read_file",
                    tool_call_id=call_id,
                )
            )
            observations.append(Observation("read_file", index != 2, f"result-{index}"))

        result = PromptWindowManager(
            PromptBudget(
                max_prompt_tokens=1_200,
                reserved_output_tokens=100,
                soft_limit_ratio=0.7,
            )
        ).prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "runtime policy"),
                conversation_history=history,
                observations=observations,
                tool_schemas=[{"name": "read_file", "arguments": {"path": "str"}}],
                conversation_initial_task="fix the parser and run tests",
            )
        )

        self.assertTrue(result.compacted)
        self.assertLess(
            result.estimated_tokens_after,
            result.estimated_tokens_before,
        )
        self.assertIsNotNone(result.conversation_history_digest)
        assert result.conversation_history_digest is not None
        self.assertTrue(result.conversation_history_digest.source_hash)
        self.assertTrue(
            any(
                "result-2" in item
                for item in result.conversation_history_digest.failed_tool_evidence
            )
        )
        roles = [message.role for message in result.llm_messages[2:]]
        for index, role in enumerate(roles):
            if role == "tool":
                self.assertGreater(index, 0)
                self.assertEqual(roles[index - 1], "assistant")

    def test_small_request_keeps_raw_history(self) -> None:
        history = [Message("user", "inspect target.py")]
        result = PromptWindowManager(PromptBudget()).prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                conversation_initial_task="inspect target.py",
            )
        )

        self.assertFalse(result.compacted)
        self.assertEqual(result.llm_messages[1:], history)
        self.assertIsNone(result.conversation_history_digest)

    def test_forced_recovery_compacts_below_soft_limit(self) -> None:
        history = [
            Message("user", "continue"),
            Message("assistant", "first analysis " + ("a" * 4_000)),
            Message("user", "more evidence"),
            Message("assistant", "second analysis " + ("b" * 4_000)),
        ]
        manager = PromptWindowManager(
            PromptBudget(max_prompt_tokens=4_000, reserved_output_tokens=200)
        )

        normal = manager.prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                conversation_initial_task="continue",
            )
        )
        forced = manager.prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                conversation_initial_task="continue",
                force_compaction=True,
            )
        )

        self.assertFalse(normal.compacted)
        self.assertTrue(forced.compacted)
        self.assertLess(
            forced.estimated_tokens_after,
            forced.estimated_tokens_before,
        )

    def test_digest_keeps_initial_task_and_later_task_updates_separate(self) -> None:
        history = [
            Message("user", "initial task", human_authority=True, origin="human"),
            Message("assistant", "a" * 3_000),
            Message(
                "user",
                "steer: preserve public API",
                human_authority=True,
                origin="operator",
            ),
            Message("assistant", "b" * 3_000),
            Message(
                "user",
                "constraint: run focused tests",
                human_authority=True,
                origin="human",
            ),
            Message("assistant", "c" * 3_000),
        ]

        result = PromptWindowManager(
            PromptBudget(
                max_prompt_tokens=1_200,
                reserved_output_tokens=100,
                soft_limit_ratio=0.5,
            )
        ).prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                conversation_initial_task="initial task",
                force_compaction=True,
            )
        )

        self.assertIsNotNone(result.conversation_history_digest)
        assert result.conversation_history_digest is not None
        self.assertEqual(result.conversation_history_digest.initial_task, "initial task")
        self.assertEqual(
            result.conversation_history_digest.task_updates,
            ["steer: preserve public API"],
        )

    def test_same_history_produces_the_same_digest_and_prompt_window(self) -> None:
        history = [
            Message("user", "initial task"),
            Message(
                "assistant",
                "inspect",
                tool_calls=[
                    {
                        "id": "read-1",
                        "name": "read_file",
                        "arguments": {"path": "target.py"},
                    }
                ],
            ),
            Message("tool", "content", tool_call_id="read-1"),
            Message("user", "preserve the public API"),
            Message("assistant", "continue " + ("x" * 4_000)),
        ]
        request = PromptWindowRequest(
            turn_system_message=Message("system", "policy"),
            conversation_history=history,
            observations=[Observation("read_file", True, "content")],
            tool_schemas=[{"name": "read_file"}],
            conversation_initial_task="initial task",
            force_compaction=True,
        )
        manager = PromptWindowManager(
            PromptBudget(max_prompt_tokens=1_200, reserved_output_tokens=100)
        )

        first = manager.prepare(request)
        second = manager.prepare(request)

        self.assertIsNotNone(first.conversation_history_digest)
        self.assertIsNotNone(second.conversation_history_digest)
        assert first.conversation_history_digest is not None
        assert second.conversation_history_digest is not None
        first_digest = first.conversation_history_digest.to_dict()
        second_digest = second.conversation_history_digest.to_dict()
        first_digest.pop("created_at")
        second_digest.pop("created_at")
        self.assertEqual(
            first_digest,
            second_digest,
        )
        self.assertEqual(
            [(message.role, message.content) for message in first.llm_messages],
            [(message.role, message.content) for message in second.llm_messages],
        )

    def test_digest_source_hash_covers_assistant_reasoning_content(self) -> None:
        def build(reasoning_content: str):
            segments = compaction_module._group_history_segments(
                [
                    Message(
                        "assistant",
                        "inspect parser",
                        reasoning_content=reasoning_content,
                    )
                ],
                [],
            )
            return compaction_module._build_digest(
                "inspect parser",
                segments,
                estimated_tokens_before=100,
                skip_initial_user_message=False,
            )

        self.assertNotEqual(
            build("reason-a").source_hash,
            build("reason-b").source_hash,
        )

    def test_digest_source_hash_covers_typed_observation_fields(self) -> None:
        messages = [
            Message(
                "assistant",
                "",
                tool_calls=[
                    {
                        "id": "read-1",
                        "name": "read_file",
                        "arguments": {"path": "target.py"},
                    }
                ],
            ),
            Message("tool", "content", tool_call_id="read-1"),
        ]

        def build(success: bool, execution_succeeded: bool | None = None):
            segments = compaction_module._group_history_segments(
                messages,
                [
                    Observation(
                        "read_file",
                        success,
                        "content",
                        execution_succeeded=execution_succeeded,
                    )
                ],
            )
            return compaction_module._build_digest(
                "inspect parser",
                segments,
                estimated_tokens_before=100,
                skip_initial_user_message=False,
            )

        successful = build(True)
        failed = build(False)
        self.assertNotEqual(successful.source_hash, failed.source_hash)
        self.assertTrue(successful.tool_transactions[0].success)
        self.assertFalse(failed.tool_transactions[0].success)
        self.assertNotEqual(
            build(True, True).source_hash,
            build(True, False).source_hash,
        )

    def test_runtime_coordination_is_not_a_human_task_update(self) -> None:
        messages = [
            Message("user", "initial", origin="human", human_authority=True),
            Message(
                "user",
                "RUNTIME COORDINATION EVIDENCE: worker ready",
                origin="runtime_coordination",
                human_authority=False,
            ),
            Message(
                "user",
                "operator steer",
                origin="operator",
                human_authority=True,
            ),
        ]
        digest = compaction_module._build_digest(
            "initial",
            compaction_module._group_history_segments(messages, []),
            estimated_tokens_before=100,
            skip_initial_user_message=True,
        )
        self.assertEqual(digest.task_updates, ["operator steer"])

    def test_next_page_merges_only_repository_supplied_uncovered_delta(self) -> None:
        manager = PromptWindowManager(
            PromptBudget(max_prompt_tokens=1_200, reserved_output_tokens=100)
        )
        initial_history = [
            Message("user", "initial task", human_authority=True, origin="human"),
            Message("assistant", "analysis-a " + ("a" * 3_000)),
            Message("user", "preserve API"),
            Message("assistant", "analysis-b " + ("b" * 3_000)),
        ]
        first = manager.prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "policy"),
                conversation_history=initial_history,
                observations=[],
                tool_schemas=[],
                conversation_initial_task="initial task",
                force_compaction=True,
            )
        )
        assert first.conversation_history_digest is not None
        self.assertGreater(first.covered_delta_count, 0)

        new_delta = [
            Message(
                "user",
                "steer: tests still use pytest",
                human_authority=True,
                origin="operator",
            ),
            Message("assistant", "analysis-c " + ("c" * 3_000)),
            Message(
                "user",
                "also run a regression test",
                human_authority=True,
                origin="human",
            ),
            Message("assistant", "analysis-d " + ("d" * 3_000)),
        ]
        next_from_original = manager.prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "policy"),
                conversation_history=new_delta,
                observations=[],
                tool_schemas=[],
                conversation_initial_task="initial task",
                previous_digest=first.conversation_history_digest,
                force_compaction=True,
            )
        )
        assert next_from_original.conversation_history_digest is not None

        self.assertGreater(next_from_original.covered_delta_count, 0)
        self.assertGreater(
            next_from_original.covered_message_count,
            first.covered_message_count,
        )
        self.assertEqual(
            next_from_original.conversation_history_digest.task_updates[
                : len(first.conversation_history_digest.task_updates)
            ],
            first.conversation_history_digest.task_updates,
        )

    def test_rolling_scan_extracts_each_new_segment_once(self) -> None:
        history = [Message("user", "initial task")]
        history.extend(
            Message("assistant", f"analysis-{index} " + (str(index) * 1_200))
            for index in range(6)
        )
        manager = PromptWindowManager(
            PromptBudget(
                max_prompt_tokens=1_200,
                reserved_output_tokens=100,
                soft_limit_ratio=0.6,
            )
        )

        with patch.object(
            compaction_module,
            "_build_digest",
            wraps=compaction_module._build_digest,
        ) as build_digest, patch.object(
            compaction_module,
            "_group_history_segments",
            wraps=compaction_module._group_history_segments,
        ) as group_segments:
            result = manager.prepare(
                PromptWindowRequest(
                    turn_system_message=Message("system", "policy"),
                    conversation_history=history,
                    observations=[],
                    tool_schemas=[],
                    conversation_initial_task="initial task",
                )
            )

        self.assertTrue(result.compacted)
        self.assertEqual(group_segments.call_count, 1)
        self.assertGreater(build_digest.call_count, 1)
        self.assertTrue(
            all(len(call.args[1]) == 1 for call in build_digest.call_args_list)
        )

    def test_selects_first_legal_prefix_that_restores_budget(self) -> None:
        history = [
            Message("user", "initial task"),
            Message("assistant", "analysis one"),
            Message("user", "preserve API"),
            Message("assistant", "analysis two"),
            Message("user", "run tests"),
        ]
        manager = PromptWindowManager(
            PromptBudget(
                max_prompt_tokens=1_000,
                reserved_output_tokens=100,
                soft_limit_ratio=0.7,
            )
        )

        def controlled_estimate(messages, tools, budget):
            del tools, budget
            has_digest = any(
                (message.content or "").startswith("conversation_history_digest")
                for message in messages
            )
            if not has_digest:
                return 900
            recent_raw_count = sum(message.role != "system" for message in messages)
            return 500 if recent_raw_count <= 2 else 700

        with patch.object(
            compaction_module,
            "estimate_prompt_tokens",
            side_effect=controlled_estimate,
        ):
            result = manager.prepare(
                PromptWindowRequest(
                    turn_system_message=Message("system", "policy"),
                    conversation_history=history,
                    observations=[],
                    tool_schemas=[],
                    conversation_initial_task="initial task",
                )
            )

        self.assertEqual(result.covered_delta_count, 3)
        self.assertEqual(result.llm_messages[2:], history[3:])


if __name__ == "__main__":
    unittest.main()
