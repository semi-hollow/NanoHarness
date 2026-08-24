from __future__ import annotations

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_forge.runtime.adapters.thread_json import JsonConversationThreadRepository
from agent_forge.runtime.adapters.task_state_json import JsonTaskStateRepository
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.domain.thread import (
    ConversationItemDraft,
    ConversationThread,
    ThreadContextState,
    ThreadRun,
    Turn,
    StableTurnContextSnapshot,
)


class ConversationThreadRepositoryTest(unittest.TestCase):
    def _repository(
        self,
        root: Path,
    ) -> tuple[JsonConversationThreadRepository, ConversationThread]:
        self._root = root
        repository = JsonConversationThreadRepository(root / "threads")
        now = time.time()
        thread = repository.create(
            ConversationThread(
                thread_id="thread-1",
                title="Parser repair",
                initial_task="Repair parser",
                workspace=str(root.resolve()),
                created_at=now,
                updated_at=now,
            )
        )
        return repository, thread

    @staticmethod
    def _turn(turn_id: str = "turn-1", task: str = "Repair parser") -> Turn:
        now = time.time()
        return Turn(
            turn_id=turn_id,
            root_task=task,
            input_item_id=f"user:{turn_id}",
            status="active",
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _user(turn: Turn, run_id: str = "run-1") -> ConversationItemDraft:
        return ConversationItemDraft(
            item_id=turn.input_item_id,
            turn_id=turn.turn_id,
            run_id=run_id,
            role="user",
            content=turn.root_task,
            origin="human",
            human_authority=True,
        )

    def _run(
        self,
        run_id: str = "run-1",
        *,
        thread_id: str = "thread-1",
        turn_id: str = "turn-1",
    ) -> ThreadRun:
        now = time.time()
        task_states = JsonTaskStateRepository(self._root / "task_state")
        task_states.start(
            TaskStartRequest(
                run_id=run_id,
                thread_id=thread_id,
                turn_id=turn_id,
                workspace=str(self._root),
                execution_workspace=str(self._root),
                execution_mode="local",
                agent_name="CodingAgent",
            )
        )
        return ThreadRun(
            run_id=run_id,
            artifact_dir=str(self._root / "runs" / run_id),
            checkpoint_path=str(task_states.path_for(run_id)),
            status=TaskRunStatus.CREATED.value,
            relationship="initial",
            created_at=now,
            updated_at=now,
        )

    def test_append_is_hash_chained_and_logically_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, _ = self._repository(Path(tmp))
            turn = self._turn()
            _, first = repository.start_turn(
                "thread-1", turn, self._user(turn), self._run()
            )
            assistant = ConversationItemDraft(
                item_id="assistant:run-1:1",
                turn_id=turn.turn_id,
                run_id="run-1",
                role="assistant",
                content="I will inspect the parser.",
                origin="model",
                human_authority=False,
            )

            second = repository.append("thread-1", assistant)
            replayed = repository.append("thread-1", assistant)

            self.assertEqual(second, replayed)
            self.assertEqual(second.sequence, 2)
            self.assertEqual(second.previous_hash, first.item_hash)
            self.assertEqual(repository.get("thread-1").sequence, 2)  # type: ignore[union-attr]
            with self.assertRaisesRegex(ValueError, "idempotency conflict"):
                repository.append(
                    "thread-1",
                    replace(assistant, content="different payload"),
                )

    def test_turn_claim_rejects_missing_bootstrap_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, _ = self._repository(root)
            turn = self._turn()
            now = time.time()
            missing = ThreadRun(
                run_id="missing-bootstrap",
                artifact_dir=str(root / "runs" / "missing-bootstrap"),
                checkpoint_path=str(
                    root / "task_state" / "missing-bootstrap.json"
                ),
                status=TaskRunStatus.CREATED.value,
                relationship="initial",
                created_at=now,
                updated_at=now,
            )

            with self.assertRaisesRegex(ValueError, "must exist before Run claim"):
                repository.start_turn(
                    "thread-1",
                    turn,
                    self._user(turn, missing.run_id),
                    missing,
                )
            loaded = repository.get("thread-1")
            assert loaded is not None
            self.assertEqual(loaded.active_turn_id, "")
            self.assertEqual(loaded.turns, ())

    def test_worker_turn_requires_non_authoritative_runtime_plan_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, thread = self._repository(Path(tmp))
            worker_thread = repository.create(
                replace(
                    thread,
                    thread_id="worker-thread",
                    thread_kind="worker",
                )
            )
            turn = self._turn("worker-turn", "Implement parser shard")
            run = self._run(
                "worker-run",
                thread_id="worker-thread",
                turn_id="worker-turn",
            )
            runtime_input = ConversationItemDraft(
                item_id=turn.input_item_id,
                turn_id=turn.turn_id,
                run_id=run.run_id,
                role="user",
                content=turn.root_task,
                origin="runtime_plan",
                human_authority=False,
            )

            _, item = repository.start_turn(
                worker_thread.thread_id,
                turn,
                runtime_input,
                run,
            )

            self.assertEqual(item.origin, "runtime_plan")
            self.assertFalse(item.human_authority)

    def test_worker_turn_rejects_human_authority_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, thread = self._repository(Path(tmp))
            worker_thread = repository.create(
                replace(
                    thread,
                    thread_id="worker-thread",
                    thread_kind="worker",
                )
            )
            turn = self._turn("worker-turn", "Implement parser shard")
            run = self._run(
                "worker-run",
                thread_id="worker-thread",
                turn_id="worker-turn",
            )

            with self.assertRaisesRegex(ValueError, "runtime_plan"):
                repository.start_turn(
                    worker_thread.thread_id,
                    turn,
                    self._user(turn, run.run_id),
                    run,
                )

    def test_start_turn_rejects_second_active_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, _ = self._repository(Path(tmp))
            first = self._turn()
            repository.start_turn(
                "thread-1", first, self._user(first), self._run()
            )
            second = self._turn("turn-2", "Add tests")

            with self.assertRaisesRegex(RuntimeError, "active turn"):
                repository.start_turn(
                    "thread-1",
                    second,
                    self._user(second, "run-2"),
                    self._run("run-2", turn_id="turn-2"),
                )

    def test_concurrent_fresh_turns_have_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, _ = self._repository(root)
            candidates = [
                (
                    self._turn("turn-a", "Repair parser"),
                    self._run("run-a", turn_id="turn-a"),
                ),
                (
                    self._turn("turn-b", "Add parser tests"),
                    self._run("run-b", turn_id="turn-b"),
                ),
            ]

            def start(candidate: tuple[Turn, ThreadRun]) -> tuple[str, str]:
                turn, run = candidate
                try:
                    JsonConversationThreadRepository(root / "threads").start_turn(
                        "thread-1",
                        turn,
                        self._user(turn, run.run_id),
                        run,
                    )
                except RuntimeError:
                    return "rejected", turn.turn_id
                return "started", turn.turn_id

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(start, candidates))

            self.assertEqual([state for state, _ in results].count("started"), 1)
            self.assertEqual([state for state, _ in results].count("rejected"), 1)
            winner = next(turn_id for state, turn_id in results if state == "started")
            loaded = repository.get("thread-1")
            assert loaded is not None
            self.assertEqual(loaded.active_turn_id, winner)
            self.assertEqual(len(loaded.turns), 1)

    def test_resume_claim_is_atomic_and_rejects_stale_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, _ = self._repository(root)
            turn = self._turn()
            repository.start_turn(
                "thread-1", turn, self._user(turn), self._run("run-1")
            )

            def claim(run_id: str) -> tuple[str, str]:
                candidate = replace(
                    self._run(run_id),
                    relationship="resume",
                    parent_run_id="run-1",
                )
                try:
                    JsonConversationThreadRepository(root / "threads").claim_resume_run(
                        "thread-1",
                        turn.turn_id,
                        expected_current_run_id="run-1",
                        run=candidate,
                    )
                except RuntimeError:
                    return "rejected", run_id
                return "claimed", run_id

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(claim, ("run-2a", "run-2b")))

            self.assertEqual([state for state, _ in results].count("claimed"), 1)
            self.assertEqual([state for state, _ in results].count("rejected"), 1)
            claimed_run_id = next(run_id for state, run_id in results if state == "claimed")
            loaded = repository.get("thread-1")
            assert loaded is not None
            self.assertEqual(loaded.require_turn(turn.turn_id).current_run_id, claimed_run_id)

            with self.assertRaisesRegex(RuntimeError, "stale Run cannot update"):
                repository.record_run(
                    "thread-1",
                    turn.turn_id,
                    replace(
                        self._run("run-1"),
                        updated_at=time.time() + 10,
                    ),
                )
            with self.assertRaisesRegex(RuntimeError, "stale Run cannot finish"):
                repository.finish_turn(
                    "thread-1",
                    turn.turn_id,
                    TaskRunStatus.FAILED.value,
                    run_id="run-1",
                )

            with self.assertRaisesRegex(RuntimeError, "stale or concurrent"):
                repository.claim_resume_run(
                    "thread-1",
                    turn.turn_id,
                    expected_current_run_id="run-1",
                    run=replace(
                        self._run("run-3"),
                        relationship="resume",
                        parent_run_id="run-1",
                    ),
                )

    def test_late_run_update_cannot_reopen_terminal_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, _ = self._repository(Path(tmp))
            turn = self._turn()
            initial = self._run("run-1")
            repository.start_turn("thread-1", turn, self._user(turn), initial)
            completed = replace(
                initial,
                status=TaskRunStatus.COMPLETED.value,
                updated_at=initial.updated_at + 1,
            )
            repository.record_run("thread-1", turn.turn_id, completed)

            with self.assertRaisesRegex(RuntimeError, "cannot be reopened"):
                repository.record_run(
                    "thread-1",
                    turn.turn_id,
                    replace(
                        initial,
                        status=TaskRunStatus.RUNNING.value,
                        updated_at=initial.updated_at + 2,
                    ),
                )

            loaded = repository.get("thread-1")
            assert loaded is not None
            self.assertEqual(
                loaded.require_turn(turn.turn_id).status,
                TaskRunStatus.COMPLETED.value,
            )

    def test_stale_run_cannot_append_new_items_after_resume_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, _ = self._repository(Path(tmp))
            turn = self._turn()
            repository.start_turn(
                "thread-1", turn, self._user(turn), self._run("run-1")
            )
            old_item = ConversationItemDraft(
                item_id="assistant:run-1:1",
                turn_id=turn.turn_id,
                run_id="run-1",
                role="assistant",
                content="durable before resume",
                origin="model",
                human_authority=False,
            )
            durable = repository.append("thread-1", old_item)
            repository.claim_resume_run(
                "thread-1",
                turn.turn_id,
                expected_current_run_id="run-1",
                run=replace(
                    self._run("run-2"),
                    relationship="resume",
                    parent_run_id="run-1",
                ),
            )

            # 已 durable item 的幂等重试仍安全；旧 Run 不能再制造新事实。
            self.assertEqual(repository.append("thread-1", old_item), durable)
            with self.assertRaisesRegex(RuntimeError, "stale Run cannot append"):
                repository.append(
                    "thread-1",
                    replace(old_item, item_id="assistant:run-1:late"),
                )

    def test_journal_ahead_turn_start_repairs_missing_turn_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, _ = self._repository(Path(tmp))
            turn = self._turn()
            real_atomic_write = (
                __import__(
                    "agent_forge.runtime.adapters.thread_json",
                    fromlist=["atomic_write_json"],
                ).atomic_write_json
            )
            failed_once = False

            def fail_thread_metadata(path: Path, payload: object) -> None:
                nonlocal failed_once
                if path.name == "thread.json" and not failed_once:
                    failed_once = True
                    raise OSError("simulated crash after journal fsync")
                real_atomic_write(path, payload)

            with patch(
                "agent_forge.runtime.adapters.thread_json.atomic_write_json",
                side_effect=fail_thread_metadata,
            ):
                with self.assertRaisesRegex(OSError, "simulated crash"):
                    repository.start_turn(
                        "thread-1",
                        turn,
                        self._user(turn),
                        self._run(),
                        snapshot=StableTurnContextSnapshot(
                            turn_id=turn.turn_id,
                            root_task=turn.root_task,
                            stable_system_prefix="frozen before Turn publish",
                        ),
                        expected_context_revision=0,
                    )

            repaired = JsonConversationThreadRepository(
                Path(tmp) / "threads"
            ).get("thread-1")
            self.assertIsNotNone(repaired)
            assert repaired is not None
            self.assertEqual(repaired.active_turn_id, turn.turn_id)
            self.assertEqual(repaired.require_turn(turn.turn_id).root_task, turn.root_task)
            self.assertEqual(repaired.latest_run.run_id, "run-1")  # type: ignore[union-attr]
            self.assertEqual(repaired.sequence, 1)
            self.assertIsNotNone(
                JsonConversationThreadRepository(
                    Path(tmp) / "threads"
                ).load_stable_turn_snapshot("thread-1", turn.turn_id)
            )

    def test_truncated_tail_is_removed_before_next_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, _ = self._repository(root)
            turn = self._turn()
            repository.start_turn(
                "thread-1", turn, self._user(turn), self._run()
            )
            journal = root / "threads" / "thread-1" / "conversation.jsonl"
            with journal.open("ab") as handle:
                handle.write(b'{"crash":')

            loaded = repository.get("thread-1")
            self.assertIsNotNone(loaded)
            self.assertIn("crash-truncated", repository.last_read_warning)
            repository.append(
                "thread-1",
                ConversationItemDraft(
                    item_id="assistant:run-1:1",
                    turn_id=turn.turn_id,
                    run_id="run-1",
                    role="assistant",
                    content="Recovered",
                    origin="model",
                    human_authority=False,
                ),
            )

            items = repository.list_items("thread-1", limit=10)
            self.assertEqual([item.sequence for item in items], [1, 2])
            self.assertTrue(journal.read_bytes().endswith(b"\n"))

    def test_complete_final_json_without_newline_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, _ = self._repository(root)
            turn = self._turn()
            repository.start_turn(
                "thread-1", turn, self._user(turn), self._run()
            )
            journal = root / "threads" / "thread-1" / "conversation.jsonl"
            journal.write_bytes(journal.read_bytes().removesuffix(b"\n"))

            repository.get("thread-1")

            self.assertEqual(
                repository.last_read_warning,
                "repaired missing final journal newline",
            )
            self.assertTrue(journal.read_bytes().endswith(b"\n"))

    def test_metadata_ahead_of_journal_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, _ = self._repository(root)
            turn = self._turn()
            repository.start_turn(
                "thread-1", turn, self._user(turn), self._run()
            )
            metadata_path = root / "threads" / "thread-1" / "thread.json"
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload["sequence"] = 2
            metadata_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "metadata is ahead"):
                repository.get("thread-1")

    def test_recent_items_returns_bounded_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, _ = self._repository(Path(tmp))
            turn = self._turn()
            repository.start_turn(
                "thread-1", turn, self._user(turn), self._run()
            )
            for index in range(5):
                repository.append(
                    "thread-1",
                    ConversationItemDraft(
                        item_id=f"assistant:run-1:{index}",
                        turn_id=turn.turn_id,
                        run_id="run-1",
                        role="assistant",
                        content=str(index),
                        origin="model",
                        human_authority=False,
                    ),
                )

            recent = repository.list_recent_items("thread-1", limit=2)
            self.assertEqual([item.content for item in recent], ["3", "4"])

    def test_context_state_uses_cas_and_stable_turn_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, _ = self._repository(Path(tmp))
            turn = self._turn()
            repository.start_turn(
                "thread-1", turn, self._user(turn), self._run()
            )
            snapshot = StableTurnContextSnapshot(
                turn_id=turn.turn_id,
                root_task=turn.root_task,
                stable_system_prefix="stable",
            )

            state = repository.save_stable_turn_snapshot(
                "thread-1",
                snapshot,
                expected_revision=0,
            )

            self.assertEqual(state.revision, 1)
            self.assertEqual(
                repository.load_stable_turn_snapshot("thread-1", turn.turn_id),
                state.snapshot_for(turn.turn_id),
            )
            with self.assertRaisesRegex(RuntimeError, "revision conflict"):
                repository.save_context_state(
                    ThreadContextState(thread_id="thread-1"),
                    expected_revision=0,
                )
            with self.assertRaisesRegex(ValueError, "immutable"):
                repository.save_stable_turn_snapshot(
                    "thread-1",
                    StableTurnContextSnapshot(
                        turn_id=turn.turn_id,
                        root_task=turn.root_task,
                        stable_system_prefix="silently changed",
                    ),
                    expected_revision=state.revision,
                )

    def test_context_digest_is_rooted_in_thread_initial_task(self) -> None:
        state = ThreadContextState(
            thread_id="thread-1",
            covered_sequence=1,
            conversation_history_digest={
                "initial_task": "thread root",
                "covered_message_count": 1,
            },
        )
        self.assertEqual(
            state.conversation_history_digest["initial_task"],
            "thread root",
        )
        with self.assertRaisesRegex(ValueError, "canonical initial_task"):
            ThreadContextState(
                thread_id="thread-1",
                conversation_history_digest={
                    "root_task": "turn-only task",
                    "covered_message_count": 0,
                },
            )


class TaskCheckpointV4Test(unittest.TestCase):
    def test_v4_owns_execution_pointers_without_copying_root_task(self) -> None:
        checkpoint = TaskCheckpoint(
            run_id="run-1",
            thread_id="thread-1",
            turn_id="turn-1",
            workspace="/tmp/repo",
            execution_workspace="/tmp/repo",
            execution_mode="local",
            status=TaskRunStatus.RUNNING.value,
        )

        payload = checkpoint.to_dict()

        self.assertEqual(payload["schema_version"], 4)
        self.assertNotIn("task", payload)
        self.assertNotIn("conversation_history_digest", payload)
        self.assertEqual(TaskCheckpoint.from_dict(dict(payload)), checkpoint)
        with self.assertRaisesRegex(ValueError, "schema_version"):
            TaskCheckpoint.from_dict({**payload, "schema_version": 3})


if __name__ == "__main__":
    unittest.main()
