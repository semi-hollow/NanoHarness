"""人工输入请求的领域数据。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

TERMINAL_HUMAN_INPUT_STATUSES = {"responded", "cancelled"}


# 核心数据：Agent 暂停后等待操作员回答的 durable 问题。
@dataclass
class HumanInputRequest:
    """等待操作员回答的一次持久问题。

    ``request_id`` 保证重复提问幂等；``thread_id`` 将 continuation 归入
    同一人工线程；
    kind/question/choices 是问题契约；answer/status 是结果；workspace/run/step/agent
    记录暂停位置，reason、note、时间和 path 提供审计信息。
    """

    request_id: str
    thread_id: str
    status: str
    kind: str
    question: str
    choices: list[str]
    answer: str
    workspace: str
    run_id: str
    step: int
    agent_name: str
    reason: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    response_note: str = ""
    path: str = ""

    def ensure_pending(self) -> None:
        """拒绝对终态或未知状态重复写入。"""

        if self.status in TERMINAL_HUMAN_INPUT_STATUSES:
            raise ValueError(f"human input request is terminal: {self.status}")
        if self.status != "pending":
            raise ValueError(
                f"human input request cannot be updated from status: {self.status}"
            )

    def record_answer(self, answer: str, note: str = "") -> None:
        """执行 pending -> responded 领域转换。"""

        self.ensure_pending()
        if self.choices and answer not in self.choices:
            raise ValueError(f"answer must be one of: {', '.join(self.choices)}")
        self.status = "responded"
        self.answer = answer
        self.response_note = note
        self.updated_at = time.time()

    def cancel(self, note: str = "") -> None:
        """执行 pending -> cancelled 领域转换。"""

        self.ensure_pending()
        self.status = "cancelled"
        self.response_note = note
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
