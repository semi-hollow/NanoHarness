import unittest

from agent_forge.context.application.compaction import (
    ContextWindowRequest,
    ContextWindowManager,
    PromptBudget,
)
from agent_forge.runtime.domain.conversation import Message, Observation


class ContextWindowManagerTest(unittest.TestCase):
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

        result = ContextWindowManager(
            PromptBudget(
                max_prompt_tokens=1_200,
                reserved_output_tokens=100,
                soft_limit_ratio=0.7,
            )
        ).prepare(
            ContextWindowRequest(
                system_message=Message("system", "runtime policy"),
                history=history,
                observations=observations,
                tools=[{"name": "read_file", "arguments": {"path": "str"}}],
                task="fix the parser and run tests",
            )
        )

        self.assertTrue(result.compacted)
        self.assertLess(
            result.estimated_tokens_after,
            result.estimated_tokens_before,
        )
        self.assertIsNotNone(result.digest)
        assert result.digest is not None
        self.assertTrue(result.digest.source_hash)
        self.assertTrue(any("result-2" in item for item in result.digest.open_failures))
        roles = [message.role for message in result.messages[2:]]
        for index, role in enumerate(roles):
            if role == "tool":
                self.assertGreater(index, 0)
                self.assertEqual(roles[index - 1], "assistant")

    def test_small_request_keeps_raw_history(self) -> None:
        history = [Message("user", "inspect target.py")]
        result = ContextWindowManager(PromptBudget()).prepare(
            ContextWindowRequest(
                system_message=Message("system", "policy"),
                history=history,
                observations=[],
                tools=[],
                task="inspect target.py",
            )
        )

        self.assertFalse(result.compacted)
        self.assertEqual(result.messages[1:], history)
        self.assertIsNone(result.digest)

    def test_forced_recovery_compacts_below_soft_limit(self) -> None:
        history = [
            Message("user", "continue"),
            Message("assistant", "first analysis " + ("a" * 4_000)),
            Message("user", "more evidence"),
            Message("assistant", "second analysis " + ("b" * 4_000)),
        ]
        manager = ContextWindowManager(
            PromptBudget(max_prompt_tokens=4_000, reserved_output_tokens=200)
        )

        normal = manager.prepare(
            ContextWindowRequest(
                system_message=Message("system", "policy"),
                history=history,
                observations=[],
                tools=[],
                task="continue",
            )
        )
        forced = manager.prepare(
            ContextWindowRequest(
                system_message=Message("system", "policy"),
                history=history,
                observations=[],
                tools=[],
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


if __name__ == "__main__":
    unittest.main()
