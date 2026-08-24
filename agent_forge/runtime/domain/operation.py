"""需 Approval 和防重复保护的持久状态变更 Operation Domain。

系统角色：区分目标、首次计划、状态迁移和可恢复记录；operation key/fingerprint 的物理
计算与 JSON 落盘由 Adapter 完成。
输入：``OperationPlan`` / ``OperationTransition``；输出：更新后的 ``OperationRecord``。

折叠导航：1 target/commands；2 record transition；3 serialization。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


# region 1. Target / Plan / Transition 命令
# 核心数据：状态变更操作指向的工具、参数和工作区目标。
@dataclass(frozen=True, kw_only=True)
class OperationTarget:
    """生成稳定 key 和目标指纹所需的最小事实。"""

    tool_name: str
    arguments: dict[str, Any]
    action: str
    workspace: str


# 核心数据：操作意图进入 planned/pending 状态所需的完整输入。
@dataclass(frozen=True, kw_only=True)
class OperationPlan:
    """将一个目标绑定到 operation key 和当前 run 位置。"""

    operation_key: str
    target: OperationTarget
    run_id: str
    step: int
    status: str = "planned"
    pre_fingerprint: dict[str, Any] | None = None


# 核心数据：已存在 OperationRecord 的一次状态迁移。
@dataclass(frozen=True, kw_only=True)
class OperationTransition:
    """批准、执行或失败时写入操作状态表的状态和执行证据。"""

    operation_key: str
    status: str
    run_id: str
    step: int
    observation: str = ""
    pre_fingerprint: dict[str, Any] | None = None
    post_fingerprint: dict[str, Any] | None = None
# endregion 1. Commands 结束


# region 2. Record transition：保留 history 与前后 fingerprint
# 核心数据：可恢复状态变更操作的状态记录和前后目标指纹。
@dataclass(kw_only=True)
class OperationRecord:
    """一次状态变更操作的幂等状态和目标指纹。

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
        """应用一次操作状态转换并保留转换历史。"""

        self.status = update.status
        self.run_id = update.run_id
        self.step = update.step
        self.updated_at = time.time()
        if update.observation:
            self.observation = update.observation
        # fresh pending 表示 stale approval 已在当前目标上重新申请；此时重新
        # 绑定当前 pre-fingerprint，供后续审批与执行证据使用。
        if update.pre_fingerprint is not None and (
            self.pre_fingerprint is None or update.status == "pending"
        ):
            self.pre_fingerprint = update.pre_fingerprint
        if update.post_fingerprint is not None:
            self.post_fingerprint = update.post_fingerprint
        if not self.history or self.history[-1] != update.status:
            self.history.append(update.status)
    # endregion 2. Record transition 结束

# region 3. 序列化
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    # endregion 3. Serialization 结束
