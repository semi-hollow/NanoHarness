"""副作用审批请求的领域数据。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


# 核心数据：Application 提交给审批仓储的待授权操作。
@dataclass(frozen=True)
class ApprovalRequestDraft:
    """创建审批请求所需的操作事实，不包含仓储生成的 key 和状态。"""

    tool_name: str
    arguments: dict[str, Any]
    action: str
    command: str
    workspace: str
    run_id: str
    step: int
    agent_name: str
    reason: str
    operation_fingerprint: dict[str, Any] | None = None


# 核心数据：绑定具体 operation fingerprint 的 durable 人工审批请求。
@dataclass
class ApprovalRequest:
    """绑定到具体 operation fingerprint 的人工授权。

    ``operation_key`` 是幂等主键；tool/action/arguments/command 描述待执行副作用；
    workspace/run/step/agent 标识来源；``operation_fingerprint`` 防止审批后目标漂移；
    status、decision_note 和时间字段保存人工决定生命周期。
    """

    operation_key: str
    status: str
    tool_name: str
    arguments: dict[str, Any]
    action: str
    command: str
    workspace: str
    run_id: str
    step: int
    agent_name: str
    reason: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    operation_fingerprint: dict[str, Any] | None = None
    decision_note: str = ""
    path: str = ""

    def decide(self, status: str, note: str = "") -> None:
        """执行 pending -> approved/rejected 转换。"""

        if status not in {"approved", "rejected"}:
            raise ValueError("approval status must be 'approved' or 'rejected'")
        self.status = status
        self.decision_note = note
        self.updated_at = time.time()

    def mark_stale(self, note: str = "") -> None:
        """标记审批因目标状态变化而不可继续使用。"""

        self.status = "stale"
        self.decision_note = note
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
