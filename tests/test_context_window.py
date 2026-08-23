import unittest

from agent_forge.context.application.compaction import (
    PromptWindowRequest,
    PromptWindowManager,
    PromptBudget,
)
from agent_forge.runtime.domain.conversation import Message, Observation


class PromptWindowManagerTest(unittest.TestCase):
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
                task="fix the parser and run tests",
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
                task="inspect target.py",
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
                task="continue",
            )
        )
        forced = manager.prepare(
            PromptWindowRequest(
                turn_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                task="continue",
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
            Message("user", "initial task"),
            Message("assistant", "a" * 3_000),
            Message("user", "steer: preserve public API"),
            Message("assistant", "b" * 3_000),
            Message("user", "constraint: run focused tests"),
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
                task="initial task",
                force_compaction=True,
            )
        )

        self.assertIsNotNone(result.conversation_history_digest)
        assert result.conversation_history_digest is not None
        self.assertEqual(result.conversation_history_digest.task, "initial task")
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
            task="initial task",
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

    def test_next_turn_merges_only_current_session_uncovered_delta(self) -> None:
        manager = PromptWindowManager(
            PromptBudget(max_prompt_tokens=1_200, reserved_output_tokens=100)
        )
        initial_history = [
            Message("user", "initial task"),
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
                task="initial task",
                force_compaction=True,
            )
        )
        assert first.conversation_history_digest is not None
        self.assertGreater(first.compacted_message_cursor, 0)

        new_delta = [
            Message("user", "steer: tests still use pytest"),
            Message("assistant", "analysis-c " + ("c" * 3_000)),
            Message("user", "also run a regression test"),
            Message("assistant", "analysis-d " + ("d" * 3_000)),
        ]
        unchanged_prefix = [*initial_history, *new_delta]
        mutated_covered_prefix = [*initial_history, *new_delta]
        mutated_covered_prefix[0] = Message(
            "user",
            "this already-covered raw text must never be rescanned",
        )

        def prepare_next(history: list[Message]):
            return manager.prepare(
                PromptWindowRequest(
                    turn_system_message=Message("system", "policy"),
                    conversation_history=history,
                    observations=[],
                    tool_schemas=[],
                    task="initial task",
                    previous_digest=first.conversation_history_digest,
                    compacted_message_cursor=first.compacted_message_cursor,
                    force_compaction=True,
                )
            )

        next_from_original = prepare_next(unchanged_prefix)
        next_from_mutated_prefix = prepare_next(mutated_covered_prefix)
        assert next_from_original.conversation_history_digest is not None
        assert next_from_mutated_prefix.conversation_history_digest is not None

        self.assertGreater(
            next_from_original.compacted_message_cursor,
            first.compacted_message_cursor,
        )
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
        self.assertEqual(
            next_from_original.conversation_history_digest.source_hash,
            next_from_mutated_prefix.conversation_history_digest.source_hash,
        )
        self.assertNotIn(
            "this already-covered raw text",
            next_from_original.conversation_history_digest.render(),
        )


if __name__ == "__main__":
    unittest.main()
