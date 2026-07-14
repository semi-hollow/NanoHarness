"""副作用审批请求的领域数据。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ApprovalRequest:
    """绑定到具体 operation fingerprint 的人工授权。"""

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
