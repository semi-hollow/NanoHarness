"""Thread / Turn / Run 的 canonical 领域状态机。

系统角色：定义持久用户会话、一次顶层请求、一次执行尝试，以及权威 Conversation
条目和 Turn 稳定上下文之间的关系；本文件不做任何文件系统读写。
输入：Repository 已读取的 mapping，或 Application 准备提交的领域字段。
输出：不可变的 ``ConversationThread``、``Turn``、``ThreadRun`` 与 Context state。
相邻边界：Application 决定何时迁移；``thread_json`` 负责 CAS、hash-chain 和落盘。

折叠导航：1 Conversation journal；2 Thread/Turn/Run 生命周期；3 Turn context state。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from agent_forge.contracts import JsonObject


ACTIVE_TURN_STATUSES = frozenset(
    {
        "active",
        "created",
        "running",
        "waiting_approval",
        "waiting_human",
        "paused",
    }
)
THREAD_KINDS = frozenset({"user", "worker", "finalizer"})


# region 1. Conversation journal：Draft → sequence/hash 身份 → durable item
def _canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, kw_only=True)
class ConversationItemDraft:
    """一次待追加消息的逻辑内容；sequence、时间和 hash 由 Repository 分配。"""

    item_id: str
    turn_id: str
    run_id: str
    role: str
    content: str
    origin: str
    human_authority: bool
    name: str | None = None
    reasoning_content: str | None = None
    tool_calls: tuple[JsonObject, ...] = ()
    tool_call_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("conversation item_id must not be empty")
        if not self.turn_id.strip():
            raise ValueError("conversation turn_id must not be empty")
        if not self.run_id.strip():
            raise ValueError("conversation run_id must not be empty")
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported conversation role: {self.role}")
        if not self.origin.strip():
            raise ValueError("conversation origin must not be empty")
        if self.human_authority and self.origin not in {"human", "operator"}:
            raise ValueError(
                "only human/operator conversation items may carry human authority"
            )

    def logical_payload(self, thread_id: str) -> JsonObject:
        """返回幂等比较使用的稳定业务 payload，不含重试时会变化的时间。"""

        return {
            "thread_id": thread_id,
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "item_id": self.item_id,
            "role": self.role,
            "content": self.content,
            "name": self.name,
            "reasoning_content": self.reasoning_content,
            "tool_calls": [dict(item) for item in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "metadata": self.metadata,
            "origin": self.origin,
            "human_authority": self.human_authority,
        }


@dataclass(frozen=True, kw_only=True)
class ConversationItem:
    """``conversation.jsonl`` 中一条带 hash-chain 身份的权威消息。"""

    sequence: int
    item_id: str
    item_hash: str
    previous_hash: str
    thread_id: str
    turn_id: str
    run_id: str
    role: str
    content: str
    origin: str
    human_authority: bool
    created_at: float
    name: str | None = None
    reasoning_content: str | None = None
    tool_calls: tuple[JsonObject, ...] = ()
    tool_call_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def from_draft(
        cls,
        *,
        thread_id: str,
        sequence: int,
        previous_hash: str,
        draft: ConversationItemDraft,
        created_at: float | None = None,
    ) -> "ConversationItem":
        """由 Repository 分配的顺序和前序 hash 构造一条不可篡改 journal item。"""

        timestamp = time.time() if created_at is None else created_at
        payload = {
            **draft.logical_payload(thread_id),
            "sequence": sequence,
            "previous_hash": previous_hash,
            "created_at": timestamp,
        }
        return cls(
            sequence=sequence,
            item_hash=_canonical_hash(payload),
            previous_hash=previous_hash,
            thread_id=thread_id,
            turn_id=draft.turn_id,
            run_id=draft.run_id,
            item_id=draft.item_id,
            role=draft.role,
            content=draft.content,
            name=draft.name,
            reasoning_content=draft.reasoning_content,
            tool_calls=tuple(dict(item) for item in draft.tool_calls),
            tool_call_id=draft.tool_call_id,
            metadata=dict(draft.metadata),
            origin=draft.origin,
            human_authority=draft.human_authority,
            created_at=timestamp,
        )

    def logical_payload(self) -> JsonObject:
        return ConversationItemDraft(
            item_id=self.item_id,
            turn_id=self.turn_id,
            run_id=self.run_id,
            role=self.role,
            content=self.content,
            name=self.name,
            reasoning_content=self.reasoning_content,
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
            metadata=self.metadata,
            origin=self.origin,
            human_authority=self.human_authority,
        ).logical_payload(self.thread_id)

    def expected_hash(self) -> str:
        return _canonical_hash(
            {
                **self.logical_payload(),
                "sequence": self.sequence,
                "previous_hash": self.previous_hash,
                "created_at": self.created_at,
            }
        )

    def to_dict(self) -> JsonObject:
        return {
            "sequence": self.sequence,
            "item_id": self.item_id,
            "item_hash": self.item_hash,
            "previous_hash": self.previous_hash,
            **self.logical_payload(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConversationItem":
        """恢复 journal item，并立即校验 identity 与 payload hash。"""

        raw_tool_calls = value.get("tool_calls")
        raw_metadata = value.get("metadata")
        tool_calls = (
            tuple(dict(item) for item in raw_tool_calls if isinstance(item, Mapping))
            if isinstance(raw_tool_calls, list)
            else ()
        )
        item = cls(
            sequence=int(value.get("sequence") or 0),
            item_id=str(value.get("item_id") or ""),
            item_hash=str(value.get("item_hash") or ""),
            previous_hash=str(value.get("previous_hash") or ""),
            thread_id=str(value.get("thread_id") or ""),
            turn_id=str(value.get("turn_id") or ""),
            run_id=str(value.get("run_id") or ""),
            role=str(value.get("role") or ""),
            content=str(value.get("content") or ""),
            name=(str(value["name"]) if value.get("name") is not None else None),
            reasoning_content=(
                str(value["reasoning_content"])
                if value.get("reasoning_content") is not None
                else None
            ),
            tool_calls=tool_calls,
            tool_call_id=(
                str(value["tool_call_id"])
                if value.get("tool_call_id") is not None
                else None
            ),
            metadata=(dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}),
            origin=str(value.get("origin") or ""),
            human_authority=bool(value.get("human_authority", False)),
            created_at=float(value.get("created_at") or 0.0),
        )
        if item.sequence < 1 or not item.thread_id or not item.item_id:
            raise ValueError("conversation item identity is incomplete")
        if item.item_hash != item.expected_hash():
            raise ValueError(f"conversation item hash mismatch: {item.item_id}")
        return item
# endregion 1. Conversation journal 结束


# region 2. Thread / Turn / Run 生命周期：唯一 active Turn 与同 Turn 多 Run
@dataclass(frozen=True, kw_only=True)
class ThreadRun:
    """一个 Turn 下不可变的 Run 导航索引；真实证据仍在 artifact 目录。"""

    run_id: str
    artifact_dir: str
    checkpoint_path: str
    status: str
    relationship: str
    parent_run_id: str = ""
    stop_reason: str = ""
    current_step: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "artifact_dir": self.artifact_dir,
            "checkpoint_path": self.checkpoint_path,
            "status": self.status,
            "relationship": self.relationship,
            "parent_run_id": self.parent_run_id,
            "stop_reason": self.stop_reason,
            "current_step": self.current_step,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ThreadRun":
        """从导航 metadata 恢复 Run；真实执行状态仍以 checkpoint 为准。"""

        return cls(
            run_id=str(value.get("run_id") or ""),
            artifact_dir=str(value.get("artifact_dir") or ""),
            checkpoint_path=str(value.get("checkpoint_path") or ""),
            status=str(value.get("status") or "unknown"),
            relationship=str(value.get("relationship") or "run"),
            parent_run_id=str(value.get("parent_run_id") or ""),
            stop_reason=str(value.get("stop_reason") or ""),
            current_step=int(value.get("current_step") or 0),
            created_at=float(value.get("created_at") or 0.0),
            updated_at=float(value.get("updated_at") or 0.0),
        )


@dataclass(frozen=True, kw_only=True)
class Turn:
    """一个用户目标及其全部恢复 Run；resume 不创建新 Turn。"""

    turn_id: str
    root_task: str
    input_item_id: str
    status: str
    created_at: float
    updated_at: float
    current_run_id: str = ""
    runs: tuple[ThreadRun, ...] = ()

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_TURN_STATUSES

    def with_run(self, run: ThreadRun) -> "Turn":
        by_id = {item.run_id: item for item in self.runs}
        by_id[run.run_id] = run
        return replace(
            self,
            status=run.status,
            current_run_id=run.run_id,
            runs=tuple(
                sorted(by_id.values(), key=lambda item: (item.created_at, item.run_id))
            ),
            updated_at=max(self.updated_at, run.updated_at),
        )

    def with_status(self, status: str, *, updated_at: float | None = None) -> "Turn":
        if status in ACTIVE_TURN_STATUSES:
            raise ValueError("finish_turn requires a terminal status")
        return replace(
            self,
            status=status,
            updated_at=time.time() if updated_at is None else updated_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "root_task": self.root_task,
            "input_item_id": self.input_item_id,
            "status": self.status,
            "current_run_id": self.current_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "runs": [run.to_dict() for run in self.runs],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Turn":
        raw_runs = value.get("runs")
        return cls(
            turn_id=str(value.get("turn_id") or ""),
            root_task=str(value.get("root_task") or ""),
            input_item_id=str(value.get("input_item_id") or ""),
            status=str(value.get("status") or "active"),
            current_run_id=str(value.get("current_run_id") or ""),
            created_at=float(value.get("created_at") or 0.0),
            updated_at=float(value.get("updated_at") or 0.0),
            runs=(
                tuple(
                    ThreadRun.from_dict(item)
                    for item in raw_runs
                    if isinstance(item, Mapping)
                )
                if isinstance(raw_runs, list)
                else ()
            ),
        )


@dataclass(frozen=True, kw_only=True)
class ConversationThread:
    """跨 Run 的唯一会话身份与 Turn/Run 导航状态。"""

    thread_id: str
    title: str
    initial_task: str
    workspace: str
    created_at: float
    updated_at: float
    thread_kind: str = "user"
    turns: tuple[Turn, ...] = ()
    active_turn_id: str = ""
    sequence: int = 0
    tail_hash: str = ""
    archived: bool = False
    pinned: bool = False

    def __post_init__(self) -> None:
        """一次性验证 Thread kind、唯一 Turn/Run 身份和 active Turn 约束。"""

        if not self.thread_id.strip():
            raise ValueError("thread_id must not be empty")
        if not self.initial_task.strip():
            raise ValueError("initial_task must not be empty")
        if self.thread_kind not in THREAD_KINDS:
            raise ValueError(f"unsupported conversation thread kind: {self.thread_kind}")
        turn_ids = [turn.turn_id for turn in self.turns]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("conversation thread contains duplicate turn ids")
        active = [turn.turn_id for turn in self.turns if turn.is_active]
        if len(active) > 1:
            raise ValueError("conversation thread may have only one active turn")
        expected_active = active[0] if active else ""
        if self.active_turn_id != expected_active:
            raise ValueError("active_turn_id does not match turn status")
        if self.sequence < 0:
            raise ValueError("conversation sequence must not be negative")
        if bool(self.sequence) != bool(self.tail_hash):
            raise ValueError("conversation sequence and tail_hash must advance together")

    @property
    def active_turn(self) -> Turn | None:
        return next(
            (turn for turn in self.turns if turn.turn_id == self.active_turn_id),
            None,
        )

    @property
    def latest_run(self) -> ThreadRun | None:
        """返回导航所需的最后一次 Run；不读取或复制 Run artifact。"""

        runs = [run for turn in self.turns for run in turn.runs]
        if not runs:
            return None
        return max(runs, key=lambda item: (item.updated_at, item.run_id))

    @property
    def runs(self) -> tuple[ThreadRun, ...]:
        """为 Console 导航展平全部 Turn 的 Run，不创建第二份持久化索引。"""

        return tuple(run for turn in self.turns for run in turn.runs)

    @property
    def latest_task(self) -> str:
        """返回最近 Turn 的 root task；空 Thread 使用 initial task。"""

        if not self.turns:
            return self.initial_task
        latest_turn = max(
            self.turns,
            key=lambda item: (item.updated_at, item.turn_id),
        )
        return latest_turn.root_task

    def require_turn(self, turn_id: str) -> Turn:
        turn = next((item for item in self.turns if item.turn_id == turn_id), None)
        if turn is None:
            raise KeyError(f"turn not found: {turn_id}")
        return turn

    def with_turn(self, turn: Turn) -> "ConversationThread":
        by_id = {item.turn_id: item for item in self.turns}
        by_id[turn.turn_id] = turn
        ordered = tuple(
            sorted(by_id.values(), key=lambda item: (item.created_at, item.turn_id))
        )
        active = [item.turn_id for item in ordered if item.is_active]
        if len(active) > 1:
            raise ValueError("cannot start a second active turn")
        return replace(
            self,
            turns=ordered,
            active_turn_id=active[0] if active else "",
            updated_at=max(self.updated_at, turn.updated_at),
        )

    def with_journal_tail(self, *, sequence: int, tail_hash: str) -> "ConversationThread":
        return replace(
            self,
            sequence=sequence,
            tail_hash=tail_hash,
            updated_at=max(self.updated_at, time.time()),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "conversation_item_schema_version": 1,
            "thread_id": self.thread_id,
            "title": self.title,
            "initial_task": self.initial_task,
            "thread_kind": self.thread_kind,
            "workspace": self.workspace,
            "active_turn_id": self.active_turn_id,
            "sequence": self.sequence,
            "tail_hash": self.tail_hash,
            "archived": self.archived,
            "pinned": self.pinned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": [turn.to_dict() for turn in self.turns],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConversationThread":
        """恢复 Thread 导航树；不在 Domain 层读取 conversation journal。"""

        if int(value.get("schema_version") or 0) != 1:
            raise ValueError("unsupported conversation thread schema_version")
        if int(value.get("conversation_item_schema_version") or 0) != 1:
            raise ValueError("unsupported conversation item schema_version")
        raw_turns = value.get("turns")
        return cls(
            thread_id=str(value.get("thread_id") or ""),
            title=str(value.get("title") or "未命名会话"),
            initial_task=str(value.get("initial_task") or ""),
            thread_kind=str(value.get("thread_kind") or "user"),
            workspace=str(value.get("workspace") or ""),
            active_turn_id=str(value.get("active_turn_id") or ""),
            sequence=int(value.get("sequence") or 0),
            tail_hash=str(value.get("tail_hash") or ""),
            archived=bool(value.get("archived", False)),
            pinned=bool(value.get("pinned", False)),
            created_at=float(value.get("created_at") or 0.0),
            updated_at=float(value.get("updated_at") or 0.0),
            turns=(
                tuple(
                    Turn.from_dict(item)
                    for item in raw_turns
                    if isinstance(item, Mapping)
                )
                if isinstance(raw_turns, list)
                else ()
            ),
        )
# endregion 2. Thread / Turn / Run 生命周期结束


# region 3. Turn context state：同 Turn immutable snapshot + 跨 Turn rolling digest
@dataclass(frozen=True, kw_only=True)
class TurnContextSnapshot:
    """同一 Turn 的稳定 System/Tool/Skill/Memory 输入快照。"""

    turn_id: str
    root_task: str
    stable_system_prefix: str
    base_tool_schemas: tuple[JsonObject, ...] = ()
    skill_tool_names: tuple[str, ...] = ()
    long_term_memory_snapshot: tuple[JsonObject, ...] = ()
    stable_context_evidence: JsonObject = field(default_factory=dict)
    contract_hash: str = ""
    updated_at: float = 0.0

    def contract_payload(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "root_task": self.root_task,
            "stable_system_prefix": self.stable_system_prefix,
            "base_tool_schemas": [dict(item) for item in self.base_tool_schemas],
            "skill_tool_names": list(self.skill_tool_names),
            "long_term_memory_snapshot": [
                dict(item) for item in self.long_term_memory_snapshot
            ],
            "stable_context_evidence": dict(self.stable_context_evidence),
        }

    def expected_contract_hash(self) -> str:
        return _canonical_hash(self.contract_payload())

    def normalized(self) -> "TurnContextSnapshot":
        expected = self.expected_contract_hash()
        if self.contract_hash and self.contract_hash != expected:
            raise ValueError("turn context snapshot contract hash mismatch")
        return replace(
            self,
            contract_hash=expected,
            updated_at=self.updated_at or time.time(),
        )

    def to_dict(self) -> dict[str, object]:
        normalized = self.normalized()
        return {
            **normalized.contract_payload(),
            "contract_hash": normalized.contract_hash,
            "updated_at": normalized.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TurnContextSnapshot":
        """恢复 stable snapshot，并让构造期 contract hash 校验拒绝漂移。"""

        raw_schemas = value.get("base_tool_schemas")
        raw_memories = value.get("long_term_memory_snapshot")
        raw_evidence = value.get("stable_context_evidence")
        snapshot = cls(
            turn_id=str(value.get("turn_id") or ""),
            root_task=str(value.get("root_task") or ""),
            stable_system_prefix=str(value.get("stable_system_prefix") or ""),
            base_tool_schemas=(
                tuple(dict(item) for item in raw_schemas if isinstance(item, Mapping))
                if isinstance(raw_schemas, list)
                else ()
            ),
            skill_tool_names=tuple(
                str(item) for item in (value.get("skill_tool_names") or [])
            ),
            long_term_memory_snapshot=(
                tuple(dict(item) for item in raw_memories if isinstance(item, Mapping))
                if isinstance(raw_memories, list)
                else ()
            ),
            stable_context_evidence=(
                dict(raw_evidence) if isinstance(raw_evidence, Mapping) else {}
            ),
            contract_hash=str(value.get("contract_hash") or ""),
            updated_at=float(value.get("updated_at") or 0.0),
        )
        if not snapshot.turn_id or not snapshot.root_task:
            raise ValueError("turn context snapshot identity is incomplete")
        return snapshot.normalized()


@dataclass(frozen=True, kw_only=True)
class ThreadContextState:
    """Conversation digest 与每 Turn 稳定快照的 CAS 持久化边界。"""

    thread_id: str
    revision: int = 0
    covered_sequence: int = 0
    conversation_history_digest: JsonObject = field(default_factory=dict)
    turn_snapshots: tuple[TurnContextSnapshot, ...] = ()
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if self.revision < 0 or self.covered_sequence < 0:
            raise ValueError("context revision/covered sequence must not be negative")
        if self.conversation_history_digest:
            if not str(self.conversation_history_digest.get("initial_task") or ""):
                raise ValueError("conversation digest requires canonical initial_task")
            raw_digest_covered = self.conversation_history_digest.get(
                "covered_message_count"
            )
            if raw_digest_covered is not None and not isinstance(
                raw_digest_covered,
                (str, int, float),
            ):
                raise ValueError("conversation digest covered count must be scalar")
            digest_covered = int(raw_digest_covered or 0)
            if digest_covered > self.covered_sequence:
                raise ValueError(
                    "conversation digest count exceeds covered journal sequence"
                )

    def snapshot_for(self, turn_id: str) -> TurnContextSnapshot | None:
        return next(
            (item for item in self.turn_snapshots if item.turn_id == turn_id),
            None,
        )

    def with_snapshot(self, snapshot: TurnContextSnapshot) -> "ThreadContextState":
        normalized = snapshot.normalized()
        by_id = {item.turn_id: item for item in self.turn_snapshots}
        existing = by_id.get(normalized.turn_id)
        if existing is not None:
            if existing.normalized().contract_hash != normalized.contract_hash:
                raise ValueError(
                    "TurnContextSnapshot is immutable after the Turn starts"
                )
            return self
        by_id[normalized.turn_id] = normalized
        return replace(
            self,
            turn_snapshots=tuple(sorted(by_id.values(), key=lambda item: item.turn_id)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "thread_id": self.thread_id,
            "revision": self.revision,
            "covered_sequence": self.covered_sequence,
            "conversation_history_digest": self.conversation_history_digest,
            "turn_snapshots": [item.to_dict() for item in self.turn_snapshots],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ThreadContextState":
        """恢复跨 Run context revision、digest coverage 与每个 Turn 的快照。"""

        if int(value.get("schema_version") or 0) != 1:
            raise ValueError("unsupported thread context state schema_version")
        raw_digest = value.get("conversation_history_digest")
        raw_snapshots = value.get("turn_snapshots")
        return cls(
            thread_id=str(value.get("thread_id") or ""),
            revision=int(value.get("revision") or 0),
            covered_sequence=int(value.get("covered_sequence") or 0),
            conversation_history_digest=(
                dict(raw_digest) if isinstance(raw_digest, Mapping) else {}
            ),
            turn_snapshots=(
                tuple(
                    TurnContextSnapshot.from_dict(item)
                    for item in raw_snapshots
                    if isinstance(item, Mapping)
                )
                if isinstance(raw_snapshots, list)
                else ()
            ),
            updated_at=float(value.get("updated_at") or 0.0),
        )
# endregion 3. Turn context state 结束


__all__ = [
    "ACTIVE_TURN_STATUSES",
    "ConversationItem",
    "ConversationItemDraft",
    "ConversationThread",
    "THREAD_KINDS",
    "ThreadContextState",
    "ThreadRun",
    "Turn",
    "TurnContextSnapshot",
]
