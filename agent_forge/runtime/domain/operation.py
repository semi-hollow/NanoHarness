"""副作用操作账本的领域数据。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class OperationRecord:
    """一次副作用操作的幂等状态和目标指纹。"""

    operation_key: str
    status: str
    tool_name: str
    arguments: dict[str, Any]
    action: str
    workspace: str
    run_id: str = ""
    step: int = 0
    observation: str = ""
    history: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    pre_fingerprint: dict[str, Any] | None = None
    post_fingerprint: dict[str, Any] | None = None
    path: str = ""

    def transition(
        self,
        status: str,
        *,
        run_id: str,
        step: int,
        pre_fingerprint: dict[str, Any] | None = None,
        post_fingerprint: dict[str, Any] | None = None,
    ) -> None:
        """应用一次账本状态转换并保留转换历史。"""

        self.status = status
        self.run_id = run_id
        self.step = step
        self.updated_at = time.time()
        if self.pre_fingerprint is None and pre_fingerprint is not None:
            self.pre_fingerprint = pre_fingerprint
        if post_fingerprint is not None:
            self.post_fingerprint = post_fingerprint
        if not self.history or self.history[-1] != status:
            self.history.append(status)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
