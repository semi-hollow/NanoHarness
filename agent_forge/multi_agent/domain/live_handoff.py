"""LIVE dependency 与跨 Worker 语义证据的最小 Domain contract。

系统角色：定义 frozen route 和 Runtime 接受的 READY/FEEDBACK/UPDATE 数据形状。
输入：Planner mapping 或 Runtime 注入身份后的事件字段。
输出：可哈希、可持久化、带 generation/attempt provenance 的事实。
本文件不拥有 mailbox、调度或授权状态；这些都属于 ``LiveHandoffRuntime``。

折叠导航：1 LIVE route；2 Event contract。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
SEMANTIC_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]+$")


# region 1. LIVE route：规定谁可向谁传递哪个 semantic key
class LiveEventType(str, Enum):
    """LIVE edge 上允许发布的三种事实。"""

    READY = "READY"
    FEEDBACK = "FEEDBACK"
    UPDATE = "UPDATE"


@dataclass(frozen=True)
class LiveDependency:
    """允许 target 在 producer 完成前消费语义证据并提前启动的边。"""

    producer_task_id: str
    target_task_id: str
    semantic_key: str

    def __post_init__(self) -> None:
        for label, value in (
            ("producer_task_id", self.producer_task_id),
            ("target_task_id", self.target_task_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"invalid {label}: {value!r}")
        if self.producer_task_id == self.target_task_id:
            raise ValueError("LIVE dependency cannot target the producer itself")
        if not SEMANTIC_KEY_PATTERN.fullmatch(self.semantic_key):
            raise ValueError("LIVE dependency requires a safe semantic_key")

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "LiveDependency":
        if not isinstance(data, dict):
            raise ValueError("LIVE dependency must be an object")
        return cls(
            producer_task_id=str(data.get("producer_task_id") or "").strip(),
            target_task_id=str(data.get("target_task_id") or "").strip(),
            semantic_key=str(data.get("semantic_key") or "").strip(),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "producer_task_id": self.producer_task_id,
            "target_task_id": self.target_task_id,
            "semantic_key": self.semantic_key,
        }
# endregion 1. LIVE route 结束


# region 2. Event contract：Runtime 绑定身份，内容校验后形成稳定 event_id
@dataclass(frozen=True)
class LiveHandoffEvent:
    """Runtime 绑定身份后接受的 READY、FEEDBACK 或 UPDATE 事实。"""

    event_type: LiveEventType
    publisher_task_id: str
    target_task_id: str
    semantic_key: str
    version: int
    summary: str
    evidence: tuple[str, ...]
    plan_generation_id: str
    worker_attempt_id: int
    caused_by_event_id: str = ""
    emitted_at: float = field(default_factory=time.time, compare=False)

    def __post_init__(self) -> None:
        """一次性校验身份、版本、内容边界和可选因果引用的物理格式。"""

        # region 1. Identity：事件类型、generation、attempt 和 Worker route
        if not isinstance(self.event_type, LiveEventType):
            raise ValueError("event_type must be READY, FEEDBACK, or UPDATE")
        if not self.plan_generation_id.strip():
            raise ValueError("event requires plan_generation_id")
        if (
            isinstance(self.worker_attempt_id, bool)
            or not isinstance(self.worker_attempt_id, int)
            or self.worker_attempt_id < 1
        ):
            raise ValueError("worker_attempt_id must be a positive integer")
        for label, value in (
            ("publisher_task_id", self.publisher_task_id),
            ("target_task_id", self.target_task_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"invalid {label}: {value!r}")
        if self.publisher_task_id == self.target_task_id:
            raise ValueError("handoff event target must be another task")
        if not SEMANTIC_KEY_PATTERN.fullmatch(self.semantic_key):
            raise ValueError("handoff event requires a safe semantic_key")
        # endregion 1. Identity 结束

        # region 2. Version 与内容：限制版本、摘要和 evidence 体积
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 1
        ):
            raise ValueError("handoff event version must be a positive integer")
        if not self.summary.strip() or len(self.summary) > 1_000:
            raise ValueError("handoff event summary must contain 1..1000 characters")
        if not self.evidence or len(self.evidence) > 8:
            raise ValueError("handoff event requires 1..8 evidence items")
        if any(not item.strip() or len(item) > 1_000 for item in self.evidence):
            raise ValueError("handoff evidence items must contain 1..1000 characters")
        # endregion 2. Version 与内容结束

        # region 3. Cause 格式：这里只验 SHA 形状，语义因果由 Runtime 对已送达事件校验
        if self.caused_by_event_id and not re.fullmatch(
            r"[a-f0-9]{64}", self.caused_by_event_id
        ):
            raise ValueError("caused_by_event_id must be a sha256 event id")
        # endregion 3. Cause 格式结束

    @property
    def event_id(self) -> str:
        payload = {
            "event_type": self.event_type.value,
            "publisher_task_id": self.publisher_task_id,
            "target_task_id": self.target_task_id,
            "semantic_key": self.semantic_key,
            "version": self.version,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "plan_generation_id": self.plan_generation_id,
            "worker_attempt_id": self.worker_attempt_id,
            "caused_by_event_id": self.caused_by_event_id,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """生成 coordination.jsonl 和模型投影共用的审计结构。"""

        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "publisher_task_id": self.publisher_task_id,
            "target_task_id": self.target_task_id,
            "semantic_key": self.semantic_key,
            "version": self.version,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "plan_generation_id": self.plan_generation_id,
            "worker_attempt_id": self.worker_attempt_id,
            "caused_by_event_id": self.caused_by_event_id,
            "emitted_at": self.emitted_at,
        }
# endregion 2. Event contract 结束
