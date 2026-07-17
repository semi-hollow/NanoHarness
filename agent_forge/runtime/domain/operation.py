"""副作用操作账本的领域数据。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


# 核心数据：副作用操作指向的工具、参数和工作区目标。
@dataclass(frozen=True)
class OperationTarget:
    """生成稳定 key 和目标指纹所需的最小事实。"""

    tool_name: str
    arguments: dict[str, Any]
    action: str
    workspace: str


# 核心数据：副作用进入 planned/pending 账本所需的完整输入。
@dataclass(frozen=True)
class OperationPlan:
    """将一个目标绑定到 operation key 和当前 run 位置。"""

    operation_key: str
    target: OperationTarget
    run_id: str
    step: int
    status: str = "planned"
    pre_fingerprint: dict[str, Any] | None = None


# 核心数据：已存在 OperationRecord 的一次状态迁移。
@dataclass(frozen=True)
class OperationTransition:
    """批准、执行或失败时写入账本的状态和执行证据。"""

    operation_key: str
    status: str
    run_id: str
    step: int
    observation: str = ""
    pre_fingerprint: dict[str, Any] | None = None
    post_fingerprint: dict[str, Any] | None = None


# 核心数据：可恢复副作用的幂等账本记录和前后目标指纹。
@dataclass
class OperationRecord:
    """一次副作用操作的幂等状态和目标指纹。

    ``operation_key`` 标识同一次意图；status/history 保存 planned/executed/failed 链；
    tool/arguments/action/workspace 描述操作；run/step/observation 记录最近执行；
    ``pre_fingerprint`` 与 ``post_fingerprint`` 用于恢复时检测重复执行或目标漂移。
    """

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

    def transition(self, update: OperationTransition) -> None:
        """应用一次账本状态转换并保留转换历史。"""

        self.status = update.status
        self.run_id = update.run_id
        self.step = update.step
        self.updated_at = time.time()
        if update.observation:
            self.observation = update.observation
        if self.pre_fingerprint is None and update.pre_fingerprint is not None:
            self.pre_fingerprint = update.pre_fingerprint
        if update.post_fingerprint is not None:
            self.post_fingerprint = update.post_fingerprint
        if not self.history or self.history[-1] != update.status:
            self.history.append(update.status)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
