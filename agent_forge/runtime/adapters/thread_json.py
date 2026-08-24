"""Conversation Thread 的本地 JSON/JSONL durable Adapter。"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from agent_forge.contracts import JsonObject
from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.runtime.domain.thread import (
    ACTIVE_TURN_STATUSES,
    ConversationItem,
    ConversationItemDraft,
    ConversationThread,
    ThreadContextState,
    ThreadRun,
    Turn,
    TurnContextSnapshot,
)
from agent_forge.runtime.domain.task import RESUMABLE_RUN_STATUSES, TaskCheckpoint
from agent_forge.runtime.ports.thread import ConversationThreadRepository


TERMINAL_TURN_STATUSES = frozenset({"cancelled", "blocked", "failed", "completed"})


class JsonConversationThreadRepository(ConversationThreadRepository):
    """每个 Thread 一个目录，并用 ``flock`` 串行化跨进程状态变化。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.last_read_warning = ""

    # region 1. Thread / Turn / Run ownership：唯一 active Turn 与 current Run CAS
    def create(self, thread: ConversationThread) -> ConversationThread:
        with self._thread_lock(thread.thread_id):
            path = self._thread_path(thread.thread_id)
            if path.exists():
                raise FileExistsError(f"conversation thread already exists: {thread.thread_id}")
            if thread.turns or thread.sequence or thread.tail_hash:
                raise ValueError("new conversation thread must not contain turns or journal state")
            atomic_write_json(path, thread.to_dict())
            return thread

    def get(self, thread_id: str) -> ConversationThread | None:
        with self._thread_lock(thread_id):
            return self._load_and_repair_unlocked(thread_id)

    def list_all(self) -> list[ConversationThread]:
        threads: list[ConversationThread] = []
        for path in sorted(self.root.glob("*/thread.json")):
            thread = self.get(path.parent.name)
            if thread is not None:
                threads.append(thread)
        return threads

    def save_metadata(self, thread: ConversationThread) -> ConversationThread:
        with self._thread_lock(thread.thread_id):
            current = self._require_thread_unlocked(thread.thread_id)
            if (
                thread.initial_task != current.initial_task
                or thread.thread_kind != current.thread_kind
                or Path(thread.workspace).resolve() != Path(current.workspace).resolve()
                or thread.turns != current.turns
                or thread.active_turn_id != current.active_turn_id
                or thread.sequence != current.sequence
                or thread.tail_hash != current.tail_hash
            ):
                raise ValueError("save_metadata may only change thread navigation fields")
            atomic_write_json(self._thread_path(thread.thread_id), thread.to_dict())
            return thread

    def start_turn(
        self,
        thread_id: str,
        turn: Turn,
        input_item: ConversationItemDraft,
        initial_run: ThreadRun,
        *,
        snapshot: TurnContextSnapshot | None = None,
        expected_context_revision: int | None = None,
    ) -> tuple[ConversationThread, ConversationItem]:
        with self._thread_lock(thread_id):
            # 1. 先证明 canonical v4 bootstrap 已 durable，并验证输入 authority/identity。
            thread = self._require_thread_unlocked(thread_id)
            self._validate_bootstrap_checkpoint_unlocked(
                thread_id=thread_id,
                turn_id=turn.turn_id,
                run=initial_run,
            )
            if input_item.turn_id != turn.turn_id:
                raise ValueError("turn input item does not target the new turn")
            self._validate_turn_start_authority(thread, input_item)
            if input_item.run_id != initial_run.run_id:
                raise ValueError("turn input and initial Run identities disagree")
            if initial_run.status != "created":
                raise ValueError("initial Turn Run must be claimed from CREATED")
            turn = turn.with_run(initial_run)
            turn_start_metadata: JsonObject = {
                "item_kind": "turn_start",
                "root_task": turn.root_task,
                "initial_run": initial_run.to_dict(),
            }
            for key, value in turn_start_metadata.items():
                existing_value = input_item.metadata.get(key)
                if existing_value is not None and existing_value != value:
                    raise ValueError(f"reserved turn-start metadata conflict: {key}")
            input_item = replace(
                input_item,
                metadata={**input_item.metadata, **turn_start_metadata},
            )

            # 2. 先拒绝身份冲突，再让可选 snapshot 成为第一个 durable Turn 事实。
            existing_turn = next(
                (item for item in thread.turns if item.turn_id == turn.turn_id),
                None,
            )
            if thread.active_turn_id and thread.active_turn_id != turn.turn_id:
                raise RuntimeError(
                    f"thread already has active turn: {thread.active_turn_id}"
                )
            if existing_turn is not None and existing_turn != turn:
                raise ValueError(f"turn idempotency conflict: {turn.turn_id}")
            candidate = thread if existing_turn is not None else thread.with_turn(turn)
            if snapshot is not None:
                if expected_context_revision is None:
                    raise ValueError(
                        "Turn snapshot requires expected_context_revision"
                    )
                normalized_snapshot = snapshot.normalized()
                if (
                    normalized_snapshot.turn_id != turn.turn_id
                    or normalized_snapshot.root_task != turn.root_task
                ):
                    raise ValueError("Turn snapshot identity does not match new Turn")
                current_context = (
                    self._load_context_state_unlocked(thread_id)
                    or ThreadContextState(thread_id=thread_id)
                )
                # snapshot 先于 turn_start durable；若上次进程死在两者之间，未被
                # Thread/journal claim 的孤立 snapshot 可在本次新 Turn 原子替换。
                claimed_turn_ids = {item.turn_id for item in thread.turns}
                current_context = replace(
                    current_context,
                    turn_snapshots=tuple(
                        item
                        for item in current_context.turn_snapshots
                        if item.turn_id in claimed_turn_ids
                        or item.turn_id == turn.turn_id
                    ),
                )
                updated_context = current_context.with_snapshot(normalized_snapshot)
                if updated_context is not current_context:
                    self._save_context_state_unlocked(
                        updated_context,
                        expected_revision=expected_context_revision,
                        known_thread=candidate,
                    )

            # 3. turn_start 先追加并 fsync journal，再原子更新 Thread metadata。
            item, candidate = self._append_unlocked(candidate, input_item)
            atomic_write_json(self._thread_path(thread_id), candidate.to_dict())
            return candidate, item

    def record_run(
        self,
        thread_id: str,
        turn_id: str,
        run: ThreadRun,
    ) -> ConversationThread:
        with self._thread_lock(thread_id):
            thread = self._require_thread_unlocked(thread_id)
            turn = thread.require_turn(turn_id)
            existing = next((item for item in turn.runs if item.run_id == run.run_id), None)
            if existing is None:
                raise ValueError(
                    f"record_run cannot introduce an unclaimed Run: {run.run_id}"
                )
            if existing is not None and existing.updated_at > run.updated_at:
                raise ValueError(f"run update moved backwards: {run.run_id}")
            if turn.current_run_id != run.run_id:
                raise RuntimeError(
                    "stale Run cannot update the current Turn: "
                    f"expected={turn.current_run_id} actual={run.run_id}"
                )
            if not turn.is_active and run.status != turn.status:
                raise RuntimeError(
                    "terminal Turn cannot be reopened or rewritten by a late Run update: "
                    f"turn={turn.status} run={run.status}"
                )
            updated = thread.with_turn(turn.with_run(run))
            atomic_write_json(self._thread_path(thread_id), updated.to_dict())
            return updated

    def claim_resume_run(
        self,
        thread_id: str,
        turn_id: str,
        *,
        expected_current_run_id: str,
        run: ThreadRun,
    ) -> ConversationThread:
        """在 Thread 锁内完成 compare-and-swap，保证只有一个 resume claimant。"""

        with self._thread_lock(thread_id):
            thread = self._require_thread_unlocked(thread_id)
            turn = thread.require_turn(turn_id)
            self._validate_bootstrap_checkpoint_unlocked(
                thread_id=thread_id,
                turn_id=turn_id,
                run=run,
            )
            if not turn.is_active:
                raise RuntimeError(f"cannot resume terminal Turn: {turn_id}")
            if turn.current_run_id != expected_current_run_id:
                raise RuntimeError(
                    "stale or concurrent resume claim rejected: "
                    f"expected={expected_current_run_id} current={turn.current_run_id}"
                )
            current_run = next(
                (item for item in turn.runs if item.run_id == expected_current_run_id),
                None,
            )
            if current_run is None or current_run.status not in RESUMABLE_RUN_STATUSES:
                raise RuntimeError(
                    f"cannot resume non-resumable current Run: {expected_current_run_id}"
                )
            if run.run_id == expected_current_run_id or any(
                item.run_id == run.run_id for item in turn.runs
            ):
                raise ValueError(f"resume Run id already exists: {run.run_id}")
            if (
                run.relationship != "resume"
                or run.parent_run_id != expected_current_run_id
                or run.status != "created"
            ):
                raise ValueError("resume Run claim metadata is invalid")
            updated = thread.with_turn(turn.with_run(run))
            atomic_write_json(self._thread_path(thread_id), updated.to_dict())
            return updated

    @staticmethod
    def _validate_bootstrap_checkpoint_unlocked(
        *,
        thread_id: str,
        turn_id: str,
        run: ThreadRun,
    ) -> TaskCheckpoint:
        """只允许 durable canonical v4 CREATED checkpoint 进入 Thread ownership。

        checkpoint 必须先于 claim 落盘。这样 Thread 一旦把 Run 设为 current，
        恢复入口就必然有一个可加载的状态起点；反向窗口只会留下未被 Thread
        引用的 orphan artifact，可由失败 claimant 安全清理。
        """

        path = Path(run.checkpoint_path).expanduser().resolve()
        if path.name != f"{run.run_id}.json":
            raise ValueError("bootstrap checkpoint path does not match Run identity")
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("bootstrap checkpoint must exist before Run claim") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("bootstrap checkpoint is not readable canonical JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("bootstrap checkpoint payload must be a JSON object")
        checkpoint = TaskCheckpoint.from_dict(raw)
        if (
            checkpoint.thread_id != thread_id
            or checkpoint.turn_id != turn_id
            or checkpoint.run_id != run.run_id
        ):
            raise ValueError("bootstrap checkpoint identity does not match Run claim")
        if checkpoint.status != "created" or run.status != "created":
            raise ValueError("Run claim requires a CREATED bootstrap checkpoint")
        return checkpoint

    def prepare_turn_terminal(
        self,
        thread_id: str,
        turn_id: str,
        *,
        run_id: str,
        status: str,
    ) -> ConversationThread:
        """先落 terminal intent，使 checkpoint durable 后的崩溃可自动收口。"""

        if status not in TERMINAL_TURN_STATUSES:
            raise ValueError("terminal intent requires a terminal status")
        with self._thread_lock(thread_id):
            thread = self._require_thread_unlocked(thread_id)
            turn = thread.require_turn(turn_id)
            if not turn.is_active:
                if turn.current_run_id == run_id and turn.status == status:
                    return thread
                raise RuntimeError("terminal intent conflicts with terminal Turn")
            if turn.current_run_id != run_id:
                raise RuntimeError(
                    "stale Run cannot prepare terminal intent: "
                    f"expected={turn.current_run_id} actual={run_id}"
                )
            payload: JsonObject = {
                "schema_version": 1,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "run_id": run_id,
                "status": status,
            }
            path = self._terminal_intent_path(thread_id, turn_id)
            if path.is_file():
                raw_existing: Any = json.loads(path.read_text(encoding="utf-8"))
                if raw_existing == payload:
                    return thread
                if (
                    isinstance(raw_existing, Mapping)
                    and str(raw_existing.get("run_id") or "") == run_id
                ):
                    raise ValueError("terminal intent status conflicts for current Run")
            atomic_write_json(path, payload)
            return thread
    # endregion 1. Thread / Turn / Run ownership结束

    # region 2. Conversation journal 与 Turn 终态提交
    def append(
        self,
        thread_id: str,
        item: ConversationItemDraft,
    ) -> ConversationItem:
        with self._thread_lock(thread_id):
            thread = self._require_thread_unlocked(thread_id)
            appended, updated = self._append_unlocked(thread, item)
            atomic_write_json(self._thread_path(thread_id), updated.to_dict())
            return appended

    def finish_turn(
        self,
        thread_id: str,
        turn_id: str,
        status: str,
        *,
        run_id: str,
    ) -> ConversationThread:
        if status not in TERMINAL_TURN_STATUSES:
            raise ValueError("finish_turn requires a terminal status")
        with self._thread_lock(thread_id):
            thread = self._require_thread_unlocked(thread_id)
            turn = thread.require_turn(turn_id)
            if turn.current_run_id != run_id:
                raise RuntimeError(
                    "stale Run cannot finish the current Turn: "
                    f"expected={turn.current_run_id} actual={run_id}"
                )
            if not turn.is_active:
                if turn.status != status:
                    raise ValueError(
                        f"turn terminal status conflict: {turn.status} != {status}"
                    )
                return thread
            current_run = next(
                (item for item in turn.runs if item.run_id == run_id),
                None,
            )
            if current_run is None:
                raise ValueError(f"current Run is missing from Turn: {run_id}")
            settled_run = replace(
                current_run,
                status=status,
                updated_at=max(current_run.updated_at, time.time()),
            )
            updated = thread.with_turn(turn.with_run(settled_run))
            atomic_write_json(self._thread_path(thread_id), updated.to_dict())
            return updated

    def get_item(self, thread_id: str, item_id: str) -> ConversationItem | None:
        with self._thread_lock(thread_id):
            self._require_thread_unlocked(thread_id)
            matched: ConversationItem | None = None

            def remember_match(item: ConversationItem) -> None:
                nonlocal matched
                if item.item_id == item_id:
                    matched = item

            self._scan_items_unlocked(thread_id, remember_match)
            return matched

    def list_items(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        turn_id: str | None = None,
        limit: int = 200,
    ) -> list[ConversationItem]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        if limit < 1:
            raise ValueError("conversation item limit must be positive")
        with self._thread_lock(thread_id):
            self._require_thread_unlocked(thread_id)
            selected: list[ConversationItem] = []

            def collect(item: ConversationItem) -> None:
                if len(selected) >= limit:
                    return
                if item.sequence <= after_sequence:
                    return
                if turn_id is not None and item.turn_id != turn_id:
                    return
                selected.append(item)

            self._scan_items_unlocked(thread_id, collect)
            return selected

    def list_recent_items(
        self,
        thread_id: str,
        *,
        turn_id: str | None = None,
        limit: int = 200,
    ) -> list[ConversationItem]:
        if limit < 1:
            raise ValueError("conversation item limit must be positive")
        with self._thread_lock(thread_id):
            self._require_thread_unlocked(thread_id)
            recent: deque[ConversationItem] = deque(maxlen=limit)

            def collect(item: ConversationItem) -> None:
                if turn_id is None or item.turn_id == turn_id:
                    recent.append(item)

            self._scan_items_unlocked(thread_id, collect)
            return list(recent)
    # endregion 2. Conversation journal 与 Turn 终态提交结束

    # region 3. Thread context state 与 immutable Turn snapshot CAS
    def load_context_state(self, thread_id: str) -> ThreadContextState | None:
        with self._thread_lock(thread_id):
            self._require_thread_unlocked(thread_id)
            return self._load_context_state_unlocked(thread_id)

    def save_context_state(
        self,
        state: ThreadContextState,
        *,
        expected_revision: int,
    ) -> ThreadContextState:
        with self._thread_lock(state.thread_id):
            return self._save_context_state_unlocked(
                state,
                expected_revision=expected_revision,
            )

    def load_turn_snapshot(
        self,
        thread_id: str,
        turn_id: str,
    ) -> TurnContextSnapshot | None:
        state = self.load_context_state(thread_id)
        return state.snapshot_for(turn_id) if state is not None else None

    def save_turn_snapshot(
        self,
        thread_id: str,
        snapshot: TurnContextSnapshot,
        *,
        expected_revision: int,
    ) -> ThreadContextState:
        if snapshot.turn_id.strip() == "":
            raise ValueError("turn snapshot requires turn_id")
        with self._thread_lock(thread_id):
            thread = self._require_thread_unlocked(thread_id)
            thread.require_turn(snapshot.turn_id)
            current = self._load_context_state_unlocked(thread_id) or ThreadContextState(
                thread_id=thread_id
            )
            updated = current.with_snapshot(snapshot)
            if updated is current:
                return current
            return self._save_context_state_unlocked(
                updated,
                expected_revision=expected_revision,
            )
    # endregion 3. Thread context state 与 Turn snapshot CAS结束

    # region 4. Journal append、崩溃修复与一致性校验
    def _append_unlocked(
        self,
        thread: ConversationThread,
        draft: ConversationItemDraft,
    ) -> tuple[ConversationItem, ConversationThread]:
        thread.require_turn(draft.turn_id)
        existing: ConversationItem | None = None

        def find_existing(item: ConversationItem) -> None:
            nonlocal existing
            if item.item_id == draft.item_id:
                existing = item

        journal_sequence, journal_tail = self._scan_items_unlocked(
            thread.thread_id,
            find_existing,
        )
        # 已存在 item_id 只允许同一 logical fact 重放；resume Run 可以接管其 run_id。
        if existing is not None:
            existing_payload = existing.logical_payload()
            draft_payload = draft.logical_payload(thread.thread_id)
            current_turn = thread.require_turn(draft.turn_id)
            replay_payload = dict(existing_payload)
            replay_payload.pop("run_id", None)
            current_payload = dict(draft_payload)
            current_payload.pop("run_id", None)
            same_fact_replayed_by_current_run = (
                draft.run_id == current_turn.current_run_id
                and replay_payload == current_payload
            )
            if existing_payload != draft_payload and not same_fact_replayed_by_current_run:
                raise ValueError(
                    f"conversation item idempotency conflict: {draft.item_id}"
                )
            return existing, thread.with_journal_tail(
                sequence=journal_sequence,
                tail_hash=journal_tail,
            )

        # 新事实只能由当前 active Turn 的 current Run 追加，旧 claimant 立即 fail closed。
        current_turn = thread.require_turn(draft.turn_id)
        if not current_turn.is_active:
            raise RuntimeError(f"cannot append to terminal turn: {draft.turn_id}")
        if draft.run_id != current_turn.current_run_id:
            raise RuntimeError(
                "stale Run cannot append a new Conversation item: "
                f"expected={current_turn.current_run_id} actual={draft.run_id}"
            )
        sequence = journal_sequence + 1
        previous_hash = journal_tail
        item = ConversationItem.from_draft(
            thread_id=thread.thread_id,
            sequence=sequence,
            previous_hash=previous_hash,
            draft=draft,
        )
        # JSONL 是 Conversation truth：单行 flush+fsync 成功后，caller 才能推进 checkpoint。
        path = self._conversation_path(thread.thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        created_file = not path.exists()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    item.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if created_file:
            self._fsync_directory(path.parent)
        return item, thread.with_journal_tail(
            sequence=item.sequence,
            tail_hash=item.item_hash,
        )

    def _require_thread_unlocked(self, thread_id: str) -> ConversationThread:
        thread = self._load_and_repair_unlocked(thread_id)
        if thread is None:
            raise KeyError(f"conversation thread not found: {thread_id}")
        return thread

    def _load_and_repair_unlocked(
        self,
        thread_id: str,
    ) -> ConversationThread | None:
        path = self._thread_path(thread_id)
        if not path.is_file():
            return None
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"thread.json root must be object: {path}")
        thread = ConversationThread.from_dict(payload)
        if thread.thread_id != thread_id:
            raise ValueError("thread directory identity does not match thread.json")
        turn_starts: list[ConversationItem] = []

        def collect_turn_start(item: ConversationItem) -> None:
            if item.metadata.get("item_kind") == "turn_start":
                turn_starts.append(item)

        # Journal 可能领先 thread.json；反向（metadata 领先）不能安全猜测，直接拒绝。
        journal_sequence, journal_tail = self._scan_items_unlocked(
            thread_id,
            collect_turn_start,
        )
        if thread.sequence > journal_sequence:
            raise ValueError("thread metadata is ahead of authoritative conversation journal")
        if thread.sequence == journal_sequence and thread.tail_hash != journal_tail:
            raise ValueError("thread metadata tail hash disagrees with conversation journal")
        repaired = self._reconcile_turn_starts_unlocked(thread, turn_starts)
        if repaired.sequence < journal_sequence:
            repaired = repaired.with_journal_tail(
                sequence=journal_sequence,
                tail_hash=journal_tail,
            )
        # terminal intent 只在匹配 current Run 的 terminal v4 checkpoint 时收口 Turn。
        repaired = self._reconcile_terminal_intent_unlocked(repaired)
        if repaired != thread:
            thread = repaired
            atomic_write_json(path, thread.to_dict())
        return thread

    def _reconcile_terminal_intent_unlocked(
        self,
        thread: ConversationThread,
    ) -> ConversationThread:
        """用 terminal intent + canonical checkpoint 修复 checkpoint/Thread 崩溃窗口。"""

        turn = thread.active_turn
        if turn is None:
            return thread
        path = self._terminal_intent_path(thread.thread_id, turn.turn_id)
        if not path.is_file():
            return thread
        raw_intent: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw_intent, Mapping):
            raise ValueError(f"terminal intent root must be object: {path}")
        if int(raw_intent.get("schema_version") or 0) != 1:
            raise ValueError("unsupported terminal intent schema_version")
        intent_thread_id = str(raw_intent.get("thread_id") or "")
        intent_turn_id = str(raw_intent.get("turn_id") or "")
        intent_run_id = str(raw_intent.get("run_id") or "")
        intent_status = str(raw_intent.get("status") or "")
        if intent_thread_id != thread.thread_id or intent_turn_id != turn.turn_id:
            raise ValueError("terminal intent identity does not match Thread path")
        if intent_status not in TERMINAL_TURN_STATUSES:
            raise ValueError("terminal intent contains a non-terminal status")
        # 新 resume Run 已经 CAS 成为 current 时，旧 intent 不再拥有收口权。
        if intent_run_id != turn.current_run_id:
            return thread
        current_run = next(
            (item for item in turn.runs if item.run_id == intent_run_id),
            None,
        )
        if current_run is None:
            raise ValueError("terminal intent references an unknown Run")
        checkpoint_path = Path(current_run.checkpoint_path)
        if not checkpoint_path.is_file():
            return thread
        raw_checkpoint: Any = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(raw_checkpoint, Mapping):
            raise ValueError("terminal intent checkpoint root must be object")
        checkpoint = TaskCheckpoint.from_dict(dict(raw_checkpoint))
        if (
            checkpoint.thread_id != thread.thread_id
            or checkpoint.turn_id != turn.turn_id
            or checkpoint.run_id != intent_run_id
        ):
            raise ValueError("terminal intent checkpoint identity mismatch")
        if checkpoint.status in ACTIVE_TURN_STATUSES:
            return thread
        if checkpoint.status != intent_status:
            raise ValueError("terminal intent status conflicts with checkpoint")
        settled_run = replace(
            current_run,
            status=checkpoint.status,
            stop_reason=checkpoint.stop_reason,
            current_step=checkpoint.current_step,
            created_at=checkpoint.created_at,
            updated_at=checkpoint.updated_at,
        )
        return thread.with_turn(turn.with_run(settled_run))

    @staticmethod
    def _validate_turn_start_authority(
        thread: ConversationThread,
        item: ConversationItemDraft | ConversationItem,
    ) -> None:
        if item.role != "user":
            raise ValueError("a new Turn must start from a user-role input")
        if thread.thread_kind == "user":
            if item.origin not in {"human", "operator"} or not item.human_authority:
                raise ValueError(
                    "a user Thread Turn must start from human-authority input"
                )
            return
        if item.origin != "runtime_plan" or item.human_authority:
            raise ValueError(
                f"a {thread.thread_kind} Thread Turn must start from "
                "non-authoritative runtime_plan input"
            )

    @staticmethod
    def _reconcile_turn_starts_unlocked(
        thread: ConversationThread,
        turn_starts: list[ConversationItem],
    ) -> ConversationThread:
        """从权威 turn-start item 修复 append 成功、metadata 未落盘的窗口。"""

        repaired = thread
        for item in turn_starts:
            JsonConversationThreadRepository._validate_turn_start_authority(
                repaired,
                item,
            )
            root_task = item.metadata.get("root_task")
            if not isinstance(root_task, str) or not root_task.strip():
                raise ValueError("turn-start item lacks canonical root_task")
            raw_initial_run = item.metadata.get("initial_run")
            if not isinstance(raw_initial_run, Mapping):
                raise ValueError("turn-start item lacks initial Run metadata")
            initial_run = ThreadRun.from_dict(raw_initial_run)
            if (
                initial_run.run_id != item.run_id
                or initial_run.status not in {"created", "running"}
            ):
                raise ValueError("turn-start initial Run metadata is invalid")
            existing = next(
                (turn for turn in repaired.turns if turn.turn_id == item.turn_id),
                None,
            )
            if existing is not None:
                if (
                    existing.input_item_id != item.item_id
                    or existing.root_task != root_task
                ):
                    raise ValueError("turn-start journal conflicts with thread metadata")
                if not any(
                    run.run_id == initial_run.run_id for run in existing.runs
                ):
                    raise ValueError("active Turn metadata omitted its initial Run")
                continue
            if item.sequence <= repaired.sequence:
                raise ValueError("thread metadata omitted an already acknowledged Turn")
            if repaired.active_turn_id:
                raise ValueError(
                    "cannot reconcile a second active Turn from conversation journal"
                )
            repaired = repaired.with_turn(
                Turn(
                    turn_id=item.turn_id,
                    root_task=root_task,
                    input_item_id=item.item_id,
                    status=initial_run.status,
                    created_at=item.created_at,
                    updated_at=item.created_at,
                ).with_run(initial_run)
            )
        return repaired

    def _scan_items_unlocked(
        self,
        thread_id: str,
        collect: Callable[[ConversationItem], None] | None = None,
    ) -> tuple[int, str]:
        """验证完整 hash chain，并允许 caller 用 deque 构造有界 tail。"""

        path = self._conversation_path(thread_id)
        self.last_read_warning = ""
        if not path.is_file():
            return 0, ""
        sequence = 0
        previous_hash = ""
        with path.open("r+b") as handle:
            while True:
                line_offset = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                complete_line = raw_line.endswith(b"\n")
                line_number = sequence + 1
                try:
                    payload: Any = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(payload, Mapping):
                        raise ValueError("conversation journal line must be an object")
                    item = ConversationItem.from_dict(payload)
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    if not complete_line:
                        self.last_read_warning = (
                            f"repaired crash-truncated final line: {exc}"
                        )
                        handle.truncate(line_offset)
                        handle.flush()
                        os.fsync(handle.fileno())
                        break
                    raise ValueError(
                        f"corrupt conversation journal at line {line_number}: {exc}"
                    ) from exc
                if item.thread_id != thread_id:
                    raise ValueError("conversation item thread_id does not match journal")
                if item.sequence != sequence + 1:
                    raise ValueError("conversation journal sequence is not contiguous")
                if item.previous_hash != previous_hash:
                    raise ValueError("conversation journal hash chain is broken")
                if not complete_line:
                    # JSON 已完整但缺 delimiter；补齐后再允许下一次 O_APPEND。
                    handle.seek(0, os.SEEK_END)
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    self.last_read_warning = "repaired missing final journal newline"
                sequence = item.sequence
                previous_hash = item.item_hash
                if collect is not None:
                    collect(item)
        return sequence, previous_hash
    # endregion 4. Journal append、崩溃修复与一致性校验结束

    # region 5. Context 序列化、进程锁与路径
    def _load_context_state_unlocked(
        self,
        thread_id: str,
    ) -> ThreadContextState | None:
        path = self._context_path(thread_id)
        if not path.is_file():
            return None
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("context_state.json root must be an object")
        state = ThreadContextState.from_dict(payload)
        if state.thread_id != thread_id:
            raise ValueError("context state thread identity mismatch")
        return state

    def _save_context_state_unlocked(
        self,
        state: ThreadContextState,
        *,
        expected_revision: int,
        known_thread: ConversationThread | None = None,
    ) -> ThreadContextState:
        thread = known_thread or self._require_thread_unlocked(state.thread_id)
        current = self._load_context_state_unlocked(state.thread_id)
        actual_revision = current.revision if current is not None else 0
        if actual_revision != expected_revision:
            raise RuntimeError(
                "context state revision conflict: "
                f"expected={expected_revision}, actual={actual_revision}"
            )
        if state.revision not in {actual_revision, actual_revision + 1}:
            raise ValueError("caller context state revision is not based on current state")
        if current is not None and state.covered_sequence < current.covered_sequence:
            raise ValueError("context covered_sequence must not move backwards")
        if state.covered_sequence > thread.sequence:
            raise ValueError("context state cannot cover conversation items that do not exist")
        for snapshot in state.turn_snapshots:
            thread.require_turn(snapshot.turn_id)
        updated = replace(
            state,
            revision=actual_revision + 1,
            updated_at=time.time(),
        )
        atomic_write_json(self._context_path(state.thread_id), updated.to_dict())
        return updated

    @contextmanager
    def _thread_lock(self, thread_id: str) -> Iterator[None]:
        directory = self._thread_directory(thread_id)
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = directory / ".lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _thread_directory(self, thread_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", thread_id):
            raise ValueError(f"invalid conversation thread id: {thread_id!r}")
        return self.root / thread_id

    def _thread_path(self, thread_id: str) -> Path:
        return self._thread_directory(thread_id) / "thread.json"

    def _conversation_path(self, thread_id: str) -> Path:
        return self._thread_directory(thread_id) / "conversation.jsonl"

    def _context_path(self, thread_id: str) -> Path:
        return self._thread_directory(thread_id) / "context_state.json"

    def _terminal_intent_path(self, thread_id: str, turn_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", turn_id):
            raise ValueError(f"invalid Turn id: {turn_id!r}")
        return self._thread_directory(thread_id) / "terminal_intents" / f"{turn_id}.json"

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    # endregion 5. Context 序列化、进程锁与路径结束


__all__ = ["JsonConversationThreadRepository"]
