"""人工输入请求的领域数据。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

TERMINAL_HUMAN_INPUT_STATUSES = {"responded", "cancelled"}


# 核心数据：Runtime 内部发起人工提问所需的最小输入。
@dataclass(frozen=True)
class HumanInputQuestion:
    """调用方描述的问题；Lifecycle 负责补齐 run、thread 和 workspace。"""

    agent_name: str
    kind: str
    question: str
    choices: tuple[str, ...]
    reason: str
    step: int
    # ask_human 使用 canonical assistant batch 中的调用身份；普通 clarification 留空。
    invocation_id: str = ""


# 核心数据：Lifecycle 提交给持久化仓储的完整问题草稿。
@dataclass(frozen=True)
class HumanInputRequestDraft:
    """创建 durable 人工问题所需的全部身份和暂停位置。"""

    thread_id: str
    turn_id: str
    kind: str
    question: str
    choices: tuple[str, ...]
    workspace: str
    run_id: str
    step: int
    agent_name: str
    reason: str
    invocation_id: str = ""


# 核心数据：Agent 暂停后等待操作员回答的 durable 问题。
@dataclass
class HumanInputRequest:
    """等待操作员回答的一次持久问题。

    ``request_id`` 保证同一 canonical invocation 的 crash resume 幂等；
    ``thread_id/turn_id`` 将 continuation 归入同一逻辑工作；
    kind/question/choices 是问题契约；answer/status 是结果；workspace/run/step/agent
    记录暂停位置，reason、note、时间和 path 提供审计信息。
    """

    request_id: str
    thread_id: str
    turn_id: str
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
    invocation_id: str = ""
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
        """执行 pending -> responded；相同回答重试幂等，冲突决定不可覆盖。"""

        if self.status == "responded":
            if self.answer == answer:
                return
            raise ValueError(
                "human input terminal decision is immutable: "
                f"responded({self.answer!r}) -> responded({answer!r})"
            )
        if self.status == "cancelled":
            raise ValueError(
                "human input terminal decision is immutable: cancelled -> responded"
            )
        self.ensure_pending()
        if self.choices and answer not in self.choices:
            raise ValueError(f"answer must be one of: {', '.join(self.choices)}")
        self.status = "responded"
        self.answer = answer
        self.response_note = note
        self.updated_at = time.time()

    def cancel(self, note: str = "") -> None:
        """执行 pending -> cancelled；重复取消幂等，既有回答不可覆盖。"""

        if self.status == "cancelled":
            return
        if self.status == "responded":
            raise ValueError(
                "human input terminal decision is immutable: responded -> cancelled"
            )
        self.ensure_pending()
        self.status = "cancelled"
        self.response_note = note
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
