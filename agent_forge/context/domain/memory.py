"""Memory 能力的稳定领域模型。

阅读本文件时先看两个核心数据：

- ``LongTermMemoryRecord``：跨 run 持久化、经过证据晋升的长期知识。
- ``SessionDigest``：会话窗口压缩后交给模型的摘要视图，不是长期真相。

本文件只定义数据、校验和状态语义，不负责召回、文件读写或模型调用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent_forge.contracts import JsonObject


class MemoryScope(str, Enum):
    """NanoHarness 当前真正支持的记忆隔离范围。"""

    WORKSPACE = "workspace"
    AGENT_PRIVATE = "agent_private"


class MemoryKind(str, Enum):
    """长期记忆保存的原子知识类型。"""

    FACT = "fact"
    DECISION = "decision"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    FAILURE_PATTERN = "failure_pattern"


class MemoryStatus(str, Enum):
    """长期记忆从候选到退役的显式生命周期。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EvidenceReference:
    """支持一条记忆的可追溯证据，而不是摘要正文的复制。

    字段说明：``source_type`` 表示证据类型；``source_id`` 是稳定标识；
    ``path`` 和 ``sha256`` 在证据来自文件时记录位置与内容指纹。
    """

    source_type: str
    source_id: str
    path: str = ""
    sha256: str = ""

    def to_dict(self) -> JsonObject:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "path": self.path,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceReference":
        return cls(
            source_type=str(data.get("source_type") or "unknown"),
            source_id=str(data.get("source_id") or ""),
            path=str(data.get("path") or ""),
            sha256=str(data.get("sha256") or ""),
        )


# 核心数据：一条长期记忆的身份、权威状态、隔离范围与失效规则。
@dataclass
class LongTermMemoryRecord:
    """可验证、可失效、可被新版本替代的一条长期记忆。

    字段说明：

    - ``memory_id``：记录主键；``namespace``：workspace 隔离键；``key``：知识主题。
    - ``kind``：fact/decision/constraint 等知识类型；``content``：实际知识正文。
    - ``scope``：workspace 或 agent_private；``agent_name``：私有记忆的可见 Agent。
    - ``status``：candidate/active/superseded/retired/rejected 权威状态。
    - ``confidence``、``importance``：透明召回排序信号，不代表正确性证明。
    - ``evidence_refs``：晋升 active 所需证据；``tags``：词项召回的补充标签。
    - ``created_at``、``updated_at``、``expires_at``：创建、更新和显式过期时间。
    - ``supersedes``：同 key 新记录替代旧记录时保存的版本链。
    """

    memory_id: str
    namespace: str
    key: str
    kind: str
    content: str
    scope: str = MemoryScope.WORKSPACE.value
    status: str = MemoryStatus.CANDIDATE.value
    confidence: float = 0.5
    importance: float = 0.5
    agent_name: str = ""
    evidence_refs: list[EvidenceReference] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    supersedes: str = ""

    def validate(self) -> None:
        """在持久化边界前检查字段和权威约束。"""

        if not self.memory_id or not self.namespace or not self.key or not self.content:
            raise ValueError("memory_id, namespace, key and content are required")
        if self.kind not in {item.value for item in MemoryKind}:
            raise ValueError(f"unsupported memory kind: {self.kind}")
        if self.scope not in {item.value for item in MemoryScope}:
            raise ValueError(f"unsupported memory scope: {self.scope}")
        if self.status not in {item.value for item in MemoryStatus}:
            raise ValueError(f"unsupported memory status: {self.status}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("memory confidence must be between 0 and 1")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("memory importance must be between 0 and 1")
        if self.scope == MemoryScope.AGENT_PRIVATE.value and not self.agent_name:
            raise ValueError("agent_private memory requires agent_name")
        if self.status == MemoryStatus.ACTIVE.value and not self.evidence_refs:
            raise ValueError("active long-term memory requires evidence")

    def is_expired(self, now: float | None = None) -> bool:
        """判断该记忆是否已经超过显式有效期。"""

        reference_time = now if now is not None else time.time()
        return self.expires_at is not None and self.expires_at <= reference_time

    def visible_to(self, namespace: str, agent_name: str) -> bool:
        """应用 workspace、状态、有效期和 agent 私有边界。"""

        if self.namespace != namespace:
            return False
        if self.status != MemoryStatus.ACTIVE.value or self.is_expired():
            return False
        if self.scope == MemoryScope.AGENT_PRIVATE.value:
            return self.agent_name == agent_name
        return True

    def render_prompt_line(self) -> str:
        """只渲染模型需要的结论和 provenance 标识。"""

        evidence = ",".join(
            f"{item.source_type}:{item.source_id}" for item in self.evidence_refs
        )
        return (
            f"[{self.kind}] {self.key}: {self.content} "
            f"(memory_id={self.memory_id}; evidence={evidence})"
        )

    def to_dict(self) -> JsonObject:
        """返回可原子写入 JSON 的稳定结构。"""

        return {
            "memory_id": self.memory_id,
            "namespace": self.namespace,
            "key": self.key,
            "kind": self.kind,
            "content": self.content,
            "scope": self.scope,
            "status": self.status,
            "confidence": self.confidence,
            "importance": self.importance,
            "agent_name": self.agent_name,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "tags": list(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "supersedes": self.supersedes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LongTermMemoryRecord":
        """在文件适配器边界恢复领域对象。"""

        raw_evidence = data.get("evidence_refs") or []
        record = cls(
            memory_id=str(data.get("memory_id") or ""),
            namespace=str(data.get("namespace") or ""),
            key=str(data.get("key") or ""),
            kind=str(data.get("kind") or ""),
            content=str(data.get("content") or ""),
            scope=str(data.get("scope") or MemoryScope.WORKSPACE.value),
            status=str(data.get("status") or MemoryStatus.CANDIDATE.value),
            confidence=float(data.get("confidence") or 0.0),
            importance=float(data.get("importance") or 0.0),
            agent_name=str(data.get("agent_name") or ""),
            evidence_refs=[
                EvidenceReference.from_dict(item)
                for item in raw_evidence
                if isinstance(item, dict)
            ],
            tags=[str(item) for item in (data.get("tags") or [])],
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            expires_at=(
                float(data["expires_at"])
                if data.get("expires_at") is not None
                else None
            ),
            supersedes=str(data.get("supersedes") or ""),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ToolTransactionDigest:
    """压缩后仍保留的一次工具事务摘要。

    ``tool_name`` 和 ``arguments_summary`` 说明做了什么；``success`` 与
    ``observation_excerpt`` 保存结果状态和有界证据片段。
    """

    tool_name: str
    arguments_summary: str
    success: bool | None
    observation_excerpt: str

    def to_dict(self) -> JsonObject:
        return {
            "tool_name": self.tool_name,
            "arguments_summary": self.arguments_summary,
            "success": self.success,
            "observation_excerpt": self.observation_excerpt,
        }


# 核心数据：旧会话被移出模型窗口后留下的结构化、可溯源摘要。
@dataclass(frozen=True)
class SessionDigest:
    """替代旧对话进入模型窗口的结构化摘要，不替代原始 trace。

    字段说明：

    - ``task``：当前任务；``covered_message_count``：本摘要覆盖的原始消息数。
    - ``source_hash``：被摘要原文的内容指纹，用于追溯而不是还原原文。
    - ``user_updates``、``assistant_updates``：保留的用户和 Agent 关键进展。
    - ``tool_transactions``：不被拆开的工具意图与结果摘要。
    - ``open_failures``：压缩时仍未解决的失败，防止恢复后重复踩坑。
    - ``estimated_tokens_before``、``estimated_tokens_after``：压缩前后预算证据。
    - ``created_at``、``schema_version``：摘要时间和持久化格式版本。
    """

    task: str
    covered_message_count: int
    source_hash: str
    user_updates: list[str]
    tool_transactions: list[ToolTransactionDigest]
    assistant_updates: list[str]
    open_failures: list[str]
    estimated_tokens_before: int
    estimated_tokens_after: int
    created_at: float = field(default_factory=time.time)
    schema_version: int = 1

    def to_dict(self) -> JsonObject:
        """返回 checkpoint 和 trace 共用的压缩契约。"""

        return {
            "schema_version": self.schema_version,
            "task": self.task,
            "covered_message_count": self.covered_message_count,
            "source_hash": self.source_hash,
            "user_updates": list(self.user_updates),
            "tool_transactions": [
                item.to_dict() for item in self.tool_transactions
            ],
            "assistant_updates": list(self.assistant_updates),
            "open_failures": list(self.open_failures),
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "created_at": self.created_at,
        }

    def render(self) -> str:
        """渲染给模型的紧凑状态，并明确其不是原始证据。"""

        transactions = "\n".join(
            "- "
            f"{item.tool_name}({item.arguments_summary}) -> "
            f"{'ok' if item.success is True else 'fail' if item.success is False else 'unknown'}: "
            f"{item.observation_excerpt}"
            for item in self.tool_transactions
        )
        return "\n".join(
            [
                "session_digest (summary only; raw trace remains authoritative):",
                f"task: {self.task}",
                f"covered_messages: {self.covered_message_count}",
                f"user_updates: {self.user_updates}",
                f"assistant_updates: {self.assistant_updates}",
                f"open_failures: {self.open_failures}",
                "tool_transactions:",
                transactions or "- none",
                f"source_hash: {self.source_hash}",
            ]
        )
