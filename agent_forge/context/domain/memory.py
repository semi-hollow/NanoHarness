"""Memory 能力的稳定领域模型。

阅读本文件时先看两个核心数据：

- ``LongTermMemoryRecord``：用户显式授权、跨 run 持久化的长期记忆。
- ``SessionDigest``：会话窗口压缩后交给模型的摘要视图，不是长期真相。

本文件只定义数据、校验和状态语义，不负责召回、文件读写或模型调用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from agent_forge.contracts import JsonObject


class MemoryScope(str, Enum):
    """一条记忆在哪些 Run 中可见。"""

    USER = "user"
    PROJECT = "project"


class MemoryStatus(str, Enum):
    """方案 2 只保留一种可用状态；删除即物理忘记。"""

    ACTIVE = "active"


class MemorySource(str, Enum):
    """记忆的授权来源；模型不能自行写入该值。"""

    USER_EXPLICIT = "user_explicit"


# user 作用域不属于任何项目，因此使用稳定专用 namespace。
USER_MEMORY_NAMESPACE = "__user__"


# 核心数据：一条长期记忆的身份、权威状态、隔离范围与失效规则。
@dataclass
class LongTermMemoryRecord:
    """用户显式保存的一条长期记忆。

    字段说明：

    - ``memory_id``：稳定主键；同 key 更新时不改变。
    - ``namespace``：用户全局命名空间，或项目的绝对路径。
    - ``key`` / ``content``：人可识别的配置键和注入 Prompt 的正文。
    - ``scope``：``user`` 或 ``project``；项目级同 key 覆盖用户级默认值。
    - ``revision``：每次显式 remember 同 key 时递增，供 Run 快照审计。
    - ``source`` 固定为 ``user_explicit``，表示模型无权自动污染跨 Run 记忆。
    """

    SCHEMA_VERSION: ClassVar[int] = 2

    memory_id: str
    namespace: str
    key: str
    content: str
    scope: str = MemoryScope.PROJECT.value
    source: str = MemorySource.USER_EXPLICIT.value
    status: str = MemoryStatus.ACTIVE.value
    revision: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def validate(self) -> None:
        """在持久化边界前检查字段和权威约束。"""

        if not self.memory_id or not self.namespace or not self.key or not self.content:
            raise ValueError("memory_id, namespace, key and content are required")
        if self.scope not in {item.value for item in MemoryScope}:
            raise ValueError(f"unsupported memory scope: {self.scope}")
        if self.source != MemorySource.USER_EXPLICIT.value:
            raise ValueError("long-term memory must be explicitly authorized by the user")
        if self.status != MemoryStatus.ACTIVE.value:
            raise ValueError(f"unsupported memory status: {self.status}")
        if self.revision < 1:
            raise ValueError("memory revision must be positive")
        if self.scope == MemoryScope.USER.value:
            if self.namespace != USER_MEMORY_NAMESPACE:
                raise ValueError("user memory must use the user namespace")
        elif self.namespace == USER_MEMORY_NAMESPACE:
            raise ValueError("project memory requires a project namespace")

    def visible_to(self, project_namespace: str) -> bool:
        """判断记忆是用户全局默认值，或当前项目的局部值。"""

        return self.scope == MemoryScope.USER.value or (
            self.scope == MemoryScope.PROJECT.value
            and self.namespace == project_namespace
        )

    def render_prompt_line(self) -> str:
        """渲染必要语义；ID 不进入 Prompt，仅进入 Trace。"""

        return f"[{self.scope}; revision={self.revision}] {self.key}: {self.content}"

    def to_dict(self) -> JsonObject:
        """返回可原子写入 JSON 的稳定结构。"""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "memory_id": self.memory_id,
            "namespace": self.namespace,
            "key": self.key,
            "content": self.content,
            "scope": self.scope,
            "source": self.source,
            "status": self.status,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LongTermMemoryRecord":
        """只恢复当前 canonical memory schema。"""

        schema_version = int(data.get("schema_version") or 0)
        if schema_version != cls.SCHEMA_VERSION:
            raise ValueError(
                "unsupported memory schema_version: "
                f"{schema_version}; migrate artifact to version {cls.SCHEMA_VERSION}"
            )

        record = cls(
            memory_id=str(data.get("memory_id") or ""),
            namespace=str(data.get("namespace") or ""),
            key=str(data.get("key") or ""),
            content=str(data.get("content") or ""),
            scope=str(data.get("scope") or MemoryScope.PROJECT.value),
            source=str(data.get("source") or ""),
            status=str(data.get("status") or MemoryStatus.ACTIVE.value),
            revision=int(data.get("revision") or 1),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
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
    """旧 Conversation History 的确定性压缩投影，不替代原始 trace。

    字段说明：

    - ``task``：初始任务；``covered_message_count``：本摘要覆盖的原始消息数。
    - ``source_hash``：被摘要原文的内容指纹，用于追溯而不是还原原文。
    - ``task_updates``：初始任务之后的用户 steer 或任务约束变化。
    - ``tool_transactions``：不被拆开的工具意图与结果摘要。
    - ``failed_tool_evidence``：历史 Tool failure 证据，不声称目前仍未解决。
    - ``estimated_tokens_before``、``estimated_tokens_after``：压缩前后预算证据。
    - ``created_at``：摘要时间；持久化版本由外层 canonical checkpoint 管理。
    """

    task: str
    covered_message_count: int
    source_hash: str
    task_updates: list[str]
    tool_transactions: list[ToolTransactionDigest]
    failed_tool_evidence: list[str]
    estimated_tokens_before: int
    estimated_tokens_after: int
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> JsonObject:
        """返回 checkpoint 和 trace 共用的压缩契约。"""

        return {
            "task": self.task,
            "covered_message_count": self.covered_message_count,
            "source_hash": self.source_hash,
            "task_updates": list(self.task_updates),
            "tool_transactions": [item.to_dict() for item in self.tool_transactions],
            "failed_tool_evidence": list(self.failed_tool_evidence),
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
                f"task_updates: {self.task_updates}",
                f"failed_tool_evidence: {self.failed_tool_evidence}",
                "tool_transactions:",
                transactions or "- none",
                f"source_hash: {self.source_hash}",
            ]
        )
