"""用户显式授权、跨 Run 持久化的长期记忆领域模型。"""

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
            raise ValueError(
                "long-term memory must be explicitly authorized by the user"
            )
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
