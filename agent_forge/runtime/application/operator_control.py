"""CLI/UI 共用的人工控制与恢复用例。"""

from __future__ import annotations

from agent_forge.runtime.domain.approval import ApprovalRequest
from agent_forge.runtime.domain.human_input import HumanInputRequest
from agent_forge.runtime.ports import ApprovalRepository, HumanInputRepository


class DecideApproval:
    """保存一次可能改变持久状态的操作审批决定。"""

    def __init__(self, approvals: ApprovalRepository) -> None:
        self.approvals = approvals

    # 主要入口：验证并持久化一次明确的状态变更操作审批决定。
    def decide(
        self,
        operation_key: str,
        decision: str,
        *,
        note: str = "",
    ) -> ApprovalRequest:
        """保存一次明确的批准或拒绝决定。"""

        return self.approvals.decide(operation_key, decision, note=note)


class RespondToHumanInput:
    """保存一次回答或取消决定，不隐式恢复 Agent。"""

    def __init__(self, human_inputs: HumanInputRepository) -> None:
        self.human_inputs = human_inputs

    # 主要入口：持久化人工回答或取消状态，不隐式重启 Agent。
    def respond(
        self,
        request_id: str,
        *,
        answer: str = "",
        cancel: bool = False,
        note: str = "",
    ) -> HumanInputRequest:
        """保存回答或取消状态，但不隐式继续 Agent。"""

        if cancel:
            return self.human_inputs.cancel(request_id, note=note)
        return self.human_inputs.respond(request_id, answer, note=note)
