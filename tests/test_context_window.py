import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent_forge.context.application.compaction as compaction_module
from agent_forge.context.application.compaction import (
    PromptWindowRequest,
    PromptWindowManager,
    PromptBudget,
)
from agent_forge.context.domain import ConversationHistoryDigest, ToolStateDigest
from agent_forge.runtime.domain.conversation import Message, Observation
from agent_forge.runtime.application.session import (
    load_transaction_safe_conversation_page,
)
from agent_forge.runtime.application.model_step_preparation import ModelStepPreparation
from agent_forge.runtime.domain.thread import ThreadContextState


def _tool_history(
    entries: list[tuple[str, dict[str, object], bool, str, bool | None]],
) -> tuple[list[Message], list[Observation]]:
    messages: list[Message] = []
    observations: list[Observation] = []
    for index, (tool_name, arguments, success, content, execution_succeeded) in enumerate(
        entries
    ):
        call_id = f"call-{index}"
        messages.extend(
            [
                Message(
                    "assistant",
                    "",
                    tool_calls=[
                        {
                            "id": call_id,
                            "name": tool_name,
                            "arguments": arguments,
                        }
                    ],
                ),
                Message(
                    "tool",
                    content,
                    name=tool_name,
                    tool_call_id=call_id,
                ),
            ]
        )
        observations.append(
            Observation(
                tool_name,
                success,
                content,
                execution_succeeded=execution_succeeded,
                validation_status=(
                    ("passed" if success else "failed")
                    if tool_name == "python_validation"
                    else None
                ),
            )
        )
    return messages, observations


def _digest_for_tool_history(
    entries: list[tuple[str, dict[str, object], bool, str, bool | None]],
) -> ConversationHistoryDigest:
    messages, observations = _tool_history(entries)
    return compaction_module._build_digest(
        "turn-current",
        "user:turn-current",
        compaction_module._group_history_segments(messages, observations),
        estimated_tokens_before=1_000,
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
                item_id=str(getattr(item, "item_id", "")),
                turn_id=str(getattr(item, "turn_id", "")),
                human_input_request_id=str(
                    getattr(item, "metadata", {}).get("human_input_request_id") or ""
                ),
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

    def test_forced_compaction_does_not_split_tool_transaction(self) -> None:
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
                model_step_system_message=Message("system", "runtime policy"),
                conversation_history=history,
                observations=observations,
                tool_schemas=[{"name": "read_file", "arguments": {"path": "str"}}],
                current_turn_id="turn-current",
                current_turn_input_item_id="user:turn-current",
                force_compaction=True,
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
        self.assertEqual(result.conversation_history_digest.state_evidence, [])
        self.assertLessEqual(
            len(result.conversation_history_digest.recent_tool_transactions),
            compaction_module.RECENT_TOOL_TRANSACTION_LIMIT,
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
                model_step_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                current_turn_id="turn-current",
                current_turn_input_item_id="user:turn-current",
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
                model_step_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                current_turn_id="turn-current",
                current_turn_input_item_id="user:turn-current",
            )
        )
        forced = manager.prepare(
            PromptWindowRequest(
                model_step_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                current_turn_id="turn-current",
                current_turn_input_item_id="user:turn-current",
                force_compaction=True,
            )
        )

        self.assertFalse(normal.compacted)
        self.assertTrue(forced.compacted)
        self.assertLess(
            forced.estimated_tokens_after,
            forced.estimated_tokens_before,
        )

    def test_digest_keeps_turn_root_out_of_current_turn_authority_updates(self) -> None:
        history = [
            Message(
                "user",
                "initial task",
                human_authority=True,
                origin="human",
                item_id="user:turn-current",
                turn_id="turn-current",
            ),
            Message("assistant", "a" * 3_000),
            Message(
                "user",
                "steer: preserve public API",
                human_authority=True,
                origin="operator",
                item_id="steer:1",
                turn_id="turn-current",
            ),
            Message("assistant", "b" * 3_000),
            Message(
                "user",
                "constraint: run focused tests",
                human_authority=True,
                origin="human",
                item_id="clarification:2",
                turn_id="turn-current",
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
                model_step_system_message=Message("system", "policy"),
                conversation_history=history,
                observations=[],
                tool_schemas=[],
                current_turn_id="turn-current",
                current_turn_input_item_id="user:turn-current",
                force_compaction=True,
            )
        )

        self.assertIsNotNone(result.conversation_history_digest)
        assert result.conversation_history_digest is not None
        self.assertEqual(
            result.conversation_history_digest.authority_turn_id,
            "turn-current",
        )
        self.assertFalse(hasattr(result.conversation_history_digest, "initial_task"))
        self.assertEqual(
            result.conversation_history_digest.authority_updates,
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
            model_step_system_message=Message("system", "policy"),
            conversation_history=history,
            observations=[Observation("read_file", True, "content")],
            tool_schemas=[{"name": "read_file"}],
            current_turn_id="turn-current",
            current_turn_input_item_id="user:turn-current",
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
                "turn-current",
                "user:turn-current",
                segments,
                estimated_tokens_before=100,
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
                "turn-current",
                "user:turn-current",
                segments,
                estimated_tokens_before=100,
            )

        successful = build(True)
        failed = build(False)
        self.assertNotEqual(successful.source_hash, failed.source_hash)
        self.assertTrue(successful.recent_tool_transactions[0].success)
        self.assertFalse(failed.recent_tool_transactions[0].success)
        self.assertNotEqual(
            build(True, True).source_hash,
            build(True, False).source_hash,
        )

    def test_runtime_coordination_is_not_a_human_authority_update(self) -> None:
        messages = [
            Message(
                "user",
                "initial",
                origin="human",
                human_authority=True,
                item_id="user:turn-current",
                turn_id="turn-current",
            ),
            Message(
                "user",
                "RUNTIME COORDINATION EVIDENCE: worker ready",
                origin="runtime_coordination",
                human_authority=False,
                item_id="coordination:1",
                turn_id="turn-current",
            ),
            Message(
                "user",
                "operator steer",
                origin="operator",
                human_authority=True,
                item_id="steer:1",
                turn_id="turn-current",
            ),
        ]
        digest = compaction_module._build_digest(
            "turn-current",
            "user:turn-current",
            compaction_module._group_history_segments(messages, []),
            estimated_tokens_before=100,
        )
        self.assertEqual(digest.authority_updates, ["operator steer"])

    def test_authority_updates_are_not_silently_evicted_by_history_length(self) -> None:
        messages = [
            Message(
                "user",
                "initial",
                origin="human",
                human_authority=True,
                item_id="user:turn-current",
                turn_id="turn-current",
            )
        ]
        messages.extend(
            Message(
                "user",
                f"authoritative constraint {index}",
                origin="operator",
                human_authority=True,
                item_id=f"steer:{index}",
                turn_id="turn-current",
            )
            for index in range(6)
        )
        previous = compaction_module._build_digest(
            "turn-current",
            "user:turn-current",
            compaction_module._group_history_segments(messages, []),
            estimated_tokens_before=1_000,
        )
        delta_messages = [
            Message(
                "user",
                f"authoritative constraint {index}",
                origin="operator",
                human_authority=True,
                item_id=f"steer:{index}",
                turn_id="turn-current",
            )
            for index in range(6, 12)
        ]
        delta = compaction_module._build_digest(
            "turn-current",
            "user:turn-current",
            compaction_module._group_history_segments(delta_messages, []),
            estimated_tokens_before=1_200,
        )
        digest = compaction_module._merge_digest(
            previous,
            delta,
            estimated_tokens_before=1_200,
        )

        self.assertEqual(
            digest.authority_updates,
            [f"authoritative constraint {index}" for index in range(12)],
        )

    def test_prompt_window_fails_closed_on_mismatched_authority_owner(self) -> None:
        turn_a_digest = ConversationHistoryDigest(
            authority_turn_id="turn-a",
            covered_message_count=20,
            source_hash="turn-a-history",
            authority_updates=["Turn A: do not change the public API"],
            resource_hints=["auth.py"],
            state_evidence=[],
            recent_tool_transactions=[],
            estimated_tokens_before=1_000,
            estimated_tokens_after=500,
        )

        with self.assertRaisesRegex(ValueError, "authority owner"):
            PromptWindowManager(PromptBudget()).prepare(
                PromptWindowRequest(
                    model_step_system_message=Message(
                        "system",
                        "Turn B root task: add regression tests",
                    ),
                    conversation_history=[
                        Message(
                            "user",
                            "add regression tests",
                            origin="human",
                            human_authority=True,
                            item_id="user:turn-b",
                            turn_id="turn-b",
                        )
                    ],
                    observations=[],
                    tool_schemas=[],
                    current_turn_id="turn-b",
                    current_turn_input_item_id="user:turn-b",
                    previous_digest=turn_a_digest,
                )
            )

    def test_same_turn_reuses_persisted_authority_updates(self) -> None:
        digest = ConversationHistoryDigest(
            authority_turn_id="turn-a",
            covered_message_count=20,
            source_hash="turn-a-history",
            authority_updates=["do not change the public API"],
            resource_hints=[],
            state_evidence=[],
            recent_tool_transactions=[],
            estimated_tokens_before=1_000,
            estimated_tokens_after=500,
        )

        result = PromptWindowManager(PromptBudget()).prepare(
            PromptWindowRequest(
                model_step_system_message=Message("system", "Turn A root task"),
                conversation_history=[Message("assistant", "continue")],
                observations=[],
                tool_schemas=[],
                current_turn_id="turn-a",
                current_turn_input_item_id="user:turn-a",
                previous_digest=digest,
            )
        )

        self.assertEqual(
            result.conversation_history_digest.authority_updates,  # type: ignore[union-attr]
            ["do not change the public API"],
        )

    def test_authority_capacity_overflow_fails_closed(self) -> None:
        messages = [
            Message(
                "user",
                "initial",
                origin="human",
                human_authority=True,
                item_id="user:turn-current",
                turn_id="turn-current",
            )
        ]
        messages.extend(
            Message(
                "user",
                f"constraint-{index}-" + (str(index) * 800),
                origin="operator",
                human_authority=True,
                item_id=f"steer:{index}",
                turn_id="turn-current",
            )
            for index in range(3)
        )
        digest = compaction_module._build_digest(
            "turn-current",
            "user:turn-current",
            compaction_module._group_history_segments(messages, []),
            estimated_tokens_before=1_000,
        )

        with self.assertRaisesRegex(
            compaction_module.DeterministicContextOverflowError,
            "authority_context_overflow",
        ):
            PromptWindowManager(
                PromptBudget(max_prompt_tokens=1_024, reserved_output_tokens=100)
            ).prepare(
                PromptWindowRequest(
                    model_step_system_message=Message("system", "policy"),
                    conversation_history=[Message("assistant", "recent tail")],
                    observations=[],
                    tool_schemas=[],
                    current_turn_id="turn-current",
                    current_turn_input_item_id="user:turn-current",
                    previous_digest=digest,
                )
            )

    def test_recent_tool_transactions_keep_latest_n_only(self) -> None:
        digest = _digest_for_tool_history(
            [
                (
                    "read_file",
                    {"path": f"file-{index}.py"},
                    True,
                    f"result-{index}",
                    None,
                )
                for index in range(compaction_module.RECENT_TOOL_TRANSACTION_LIMIT + 4)
            ]
        )

        self.assertEqual(
            [item.observation_excerpt for item in digest.recent_tool_transactions],
            [
                f"result-{index}"
                for index in range(4, compaction_module.RECENT_TOOL_TRANSACTION_LIMIT + 4)
            ],
        )

    def test_read_and_search_observations_do_not_become_durable_state(self) -> None:
        digest = _digest_for_tool_history(
            [
                ("read_file", {"path": "auth.py"}, False, "old source text", None),
                (
                    "grep_search",
                    {"path": "tests", "keyword": "TokenManager"},
                    True,
                    "tests/test_auth.py:12",
                    None,
                ),
            ]
        )

        self.assertEqual(digest.state_evidence, [])
        self.assertEqual(digest.resource_hints, ["auth.py", "tests", "TokenManager"])
        self.assertEqual(len(digest.recent_tool_transactions), 2)

    def test_generic_run_command_is_only_a_recent_breadcrumb(self) -> None:
        digest = _digest_for_tool_history(
            [
                (
                    "run_command",
                    {"command": "pytest tests/test_auth.py"},
                    False,
                    "exit_code=1",
                    True,
                )
            ]
        )

        self.assertEqual(digest.state_evidence, [])
        self.assertEqual(
            [item.tool_name for item in digest.recent_tool_transactions],
            ["run_command"],
        )

    def test_validation_fail_then_pass_supersedes_failure_across_merge(self) -> None:
        arguments = {"check_type": "pytest", "validation_target": "tests/test_auth.py"}
        failed = _digest_for_tool_history(
            [
                (
                    "python_validation",
                    arguments,
                    False,
                    "validation_command=pytest tests/test_auth.py\n1 failed",
                    True,
                )
            ]
        )
        passed = _digest_for_tool_history(
            [
                (
                    "python_validation",
                    arguments,
                    True,
                    "validation_command=pytest tests/test_auth.py\n1 passed",
                    True,
                )
            ]
        )

        self.assertEqual([item.status for item in failed.state_evidence], ["failed"])
        merged = compaction_module._merge_digest(
            failed,
            passed,
            estimated_tokens_before=2_000,
        )
        self.assertEqual(len(merged.state_evidence), 1)
        self.assertEqual(merged.state_evidence[0].status, "passed")

    def test_successful_edit_invalidates_prior_validation_until_revalidated(self) -> None:
        validation_arguments = {
            "check_type": "pytest",
            "validation_target": "tests/test_auth.py",
        }
        passed = _digest_for_tool_history(
            [
                (
                    "python_validation",
                    validation_arguments,
                    True,
                    "1 passed",
                    True,
                )
            ]
        )
        edit = _digest_for_tool_history(
            [
                (
                    "replace_text",
                    {"path": "auth.py", "old": "before", "new": "after"},
                    True,
                    "replaced text once: auth.py",
                    None,
                )
            ]
        )

        after_edit = compaction_module._merge_digest(
            passed,
            edit,
            estimated_tokens_before=2_000,
        )

        self.assertTrue(after_edit.invalidates_prior_validation)
        self.assertEqual(after_edit.state_evidence, [])

        persisted = after_edit.to_dict()
        self.assertNotIn("invalidates_prior_validation", persisted)
        self.assertFalse(
            ConversationHistoryDigest.from_dict(
                dict(persisted)
            ).invalidates_prior_validation
        )

        revalidated = _digest_for_tool_history(
            [
                (
                    "python_validation",
                    validation_arguments,
                    True,
                    "1 passed after edit",
                    True,
                )
            ]
        )
        after_revalidation = compaction_module._merge_digest(
            after_edit,
            revalidated,
            estimated_tokens_before=2_200,
        )
        self.assertEqual(after_revalidation.state_evidence[0].status, "passed")

    def test_distinct_validation_keys_keep_independent_latest_state(self) -> None:
        digest = _digest_for_tool_history(
            [
                (
                    "python_validation",
                    {"check_type": "pytest", "validation_target": "tests/test_auth.py"},
                    False,
                    "1 failed",
                    True,
                ),
                (
                    "python_validation",
                    {"check_type": "pytest", "validation_target": "tests/test_api.py"},
                    True,
                    "1 passed",
                    True,
                ),
            ]
        )

        self.assertEqual(len(digest.state_evidence), 2)
        self.assertNotEqual(
            digest.state_evidence[0].state_key,
            digest.state_evidence[1].state_key,
        )
        self.assertEqual(
            [
                (item.validation_target, item.status)
                for item in digest.state_evidence
            ],
            [("tests/test_auth.py", "failed"), ("tests/test_api.py", "passed")],
        )

    def test_validation_blocked_uses_typed_status_not_observation_text(self) -> None:
        messages, _ = _tool_history(
            [
                (
                    "python_validation",
                    {"check_type": "pytest", "validation_target": "tests"},
                    True,
                    "environment unavailable",
                    None,
                )
            ]
        )
        digest = compaction_module._build_digest(
            "turn-current",
            "user:turn-current",
            compaction_module._group_history_segments(
                messages,
                [
                    Observation(
                        "python_validation",
                        True,
                        "environment unavailable",
                        validation_status="blocked",
                    )
                ],
            ),
            estimated_tokens_before=1_000,
        )

        self.assertEqual(digest.state_evidence[0].status, "blocked")

    def test_digest_round_trip_has_one_validation_state_authority(self) -> None:
        digest = _digest_for_tool_history(
            [
                (
                    "python_validation",
                    {"check_type": "pytest", "validation_target": "tests"},
                    False,
                    "1 failed",
                    True,
                )
            ]
        )

        payload = digest.to_dict()
        restored = ConversationHistoryDigest.from_dict(dict(payload))

        self.assertNotIn("unresolved_failures", payload)
        self.assertEqual(payload["schema_version"], 4)
        self.assertNotIn("invalidates_prior_validation", payload)
        self.assertEqual(restored.state_evidence, digest.state_evidence)
        self.assertFalse(hasattr(restored, "unresolved_failures"))
        with self.assertRaisesRegex(ValueError, "unsupported.*schema_version"):
            ConversationHistoryDigest.from_dict({**payload, "schema_version": 3})
        with self.assertRaisesRegex(ValueError, "unsupported tool state status"):
            ToolStateDigest(
                state_key="old-stale-state",
                tool_name="python_validation",
                check_type="pytest",
                validation_target="tests",
                status="stale",
                observation_excerpt="historical",
            )

    def test_resource_hints_are_deduplicated_and_latest_n_bounded(self) -> None:
        entries = [
            ("read_file", {"path": f"file-{index}.py"}, True, "content", None)
            for index in range(compaction_module.RESOURCE_HINT_LIMIT + 6)
        ]
        entries.append(("read_file", {"path": "file-0.py"}, True, "again", None))

        digest = _digest_for_tool_history(entries)

        self.assertEqual(len(digest.resource_hints), compaction_module.RESOURCE_HINT_LIMIT)
        self.assertEqual(digest.resource_hints[-1], "file-0.py")
        self.assertNotIn("file-1.py", digest.resource_hints)

    def test_distinct_state_overflow_fails_closed_instead_of_evicting(self) -> None:
        state_evidence = [
            ToolStateDigest(
                state_key=f"validate:key-{index}",
                tool_name="python_validation",
                check_type="pytest",
                validation_target=f"target-{index}",
                status="passed",
                observation_excerpt="ok",
            )
            for index in range(compaction_module.STATE_EVIDENCE_LIMIT + 1)
        ]
        digest = ConversationHistoryDigest(
            authority_turn_id="turn-current",
            covered_message_count=len(state_evidence),
            source_hash="state-overflow",
            authority_updates=[],
            resource_hints=[],
            state_evidence=state_evidence,
            recent_tool_transactions=[],
            estimated_tokens_before=1_000,
            estimated_tokens_after=500,
        )

        with self.assertRaisesRegex(
            compaction_module.DeterministicContextOverflowError,
            "state_evidence_overflow",
        ):
            PromptWindowManager(PromptBudget()).prepare(
                PromptWindowRequest(
                    model_step_system_message=Message("system", "policy"),
                    conversation_history=[Message("assistant", "tail")],
                    observations=[],
                    tool_schemas=[],
                    current_turn_id="turn-current",
                    current_turn_input_item_id="user:turn-current",
                    previous_digest=digest,
                )
            )

    def test_next_page_merges_only_repository_supplied_uncovered_delta(self) -> None:
        manager = PromptWindowManager(
            PromptBudget(max_prompt_tokens=1_200, reserved_output_tokens=100)
        )
        initial_history = [
            Message(
                "user",
                "initial task",
                human_authority=True,
                origin="human",
                item_id="user:turn-current",
                turn_id="turn-current",
            ),
            Message("assistant", "analysis-a " + ("a" * 3_000)),
            Message("user", "preserve API"),
            Message("assistant", "analysis-b " + ("b" * 3_000)),
        ]
        first = manager.prepare(
            PromptWindowRequest(
                model_step_system_message=Message("system", "policy"),
                conversation_history=initial_history,
                observations=[],
                tool_schemas=[],
                current_turn_id="turn-current",
                current_turn_input_item_id="user:turn-current",
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
                item_id="steer:pytest",
                turn_id="turn-current",
            ),
            Message("assistant", "analysis-c " + ("c" * 3_000)),
            Message(
                "user",
                "also run a regression test",
                human_authority=True,
                origin="human",
                item_id="clarification:regression",
                turn_id="turn-current",
            ),
            Message("assistant", "analysis-d " + ("d" * 3_000)),
        ]
        next_from_original = manager.prepare(
            PromptWindowRequest(
                model_step_system_message=Message("system", "policy"),
                conversation_history=new_delta,
                observations=[],
                tool_schemas=[],
                current_turn_id="turn-current",
                current_turn_input_item_id="user:turn-current",
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
            next_from_original.conversation_history_digest.authority_updates[
                : len(first.conversation_history_digest.authority_updates)
            ],
            first.conversation_history_digest.authority_updates,
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
                    model_step_system_message=Message("system", "policy"),
                    conversation_history=history,
                    observations=[],
                    tool_schemas=[],
                    current_turn_id="turn-current",
                    current_turn_input_item_id="user:turn-current",
                )
            )

        self.assertTrue(result.compacted)
        self.assertEqual(group_segments.call_count, 1)
        self.assertGreater(build_digest.call_count, 1)
        self.assertTrue(
            all(len(call.args[2]) == 1 for call in build_digest.call_args_list)
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
                    model_step_system_message=Message("system", "policy"),
                    conversation_history=history,
                    observations=[],
                    tool_schemas=[],
                    current_turn_id="turn-current",
                    current_turn_input_item_id="user:turn-current",
                )
            )

        self.assertEqual(result.covered_delta_count, 3)
        self.assertEqual(result.llm_messages[2:], history[3:])

    def test_context_state_cas_conflict_fails_closed_before_checkpoint_update(self) -> None:
        class ConflictingRepository:
            def save_context_state(self, state, *, expected_revision):
                del state, expected_revision
                raise RuntimeError("context state revision conflict")

        class Lifecycle:
            checkpoint_updated = False

            def update_checkpoint(self, update):
                del update
                self.checkpoint_updated = True

        lifecycle = Lifecycle()
        session = SimpleNamespace(
            message_sequences=[1],
            conversation_threads=ConflictingRepository(),
            context_revision=2,
            lifecycle=lifecycle,
        )
        digest = ConversationHistoryDigest(
            authority_turn_id="turn-current",
            covered_message_count=1,
            source_hash="source",
            authority_updates=[],
            resource_hints=[],
            state_evidence=[],
            recent_tool_transactions=[],
            estimated_tokens_before=100,
            estimated_tokens_after=50,
        )

        with self.assertRaisesRegex(RuntimeError, "model input was not committed"):
            ModelStepPreparation._save_thread_digest(
                ModelStepPreparation.__new__(ModelStepPreparation),
                session=session,
                state=ThreadContextState(
                    thread_id="thread-1",
                    revision=2,
                ),
                digest=digest,
                covered_delta_count=1,
            )

        self.assertEqual(session.context_revision, 2)
        self.assertFalse(lifecycle.checkpoint_updated)


if __name__ == "__main__":
    unittest.main()
