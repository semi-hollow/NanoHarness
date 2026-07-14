"""CLI/UI 共用的人工控制与恢复用例。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.runtime.domain.approval import ApprovalRequest
from agent_forge.runtime.domain.human_input import HumanInputRequest
from agent_forge.runtime.domain.task import TaskCheckpoint
from agent_forge.runtime.ports import ApprovalRepository, HumanInputRepository


@dataclass(frozen=True)
class ContinuationPlan:
    """恢复新 run 所需的显式输入，不包含隐藏进程状态。"""

    task: str
    workspace: str
    human_thread_id: str


class DecideApproval:
    """保存一次副作用审批决定。"""

    def __init__(self, approvals: ApprovalRepository) -> None:
        self.approvals = approvals

    # PRIMARY ENTRYPOINT: persist one operator decision without executing a tool.
    def execute(
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

    # PRIMARY ENTRYPOINT: persist one answer or cancellation without resuming.
    def execute(
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


class BuildContinuationPlan:
    """从 durable checkpoint 与人工回答构造一个新的显式 run。"""

    def __init__(self, human_inputs: HumanInputRepository) -> None:
        self.human_inputs = human_inputs

    # PRIMARY ENTRYPOINT: build explicit inputs for a new continuation run.
    def execute(
        self,
        checkpoint: TaskCheckpoint,
        *,
        override_task: str = "",
        workspace: str = "",
    ) -> ContinuationPlan:
        """构造新 run 所需的 task、workspace 和 human thread。"""

        metadata = checkpoint.metadata if isinstance(checkpoint.metadata, dict) else {}
        thread_id = str(metadata.get("human_thread_id") or checkpoint.run_id)
        task = override_task or f"continue previous task: {checkpoint.task}"
        request_id = str(metadata.get("human_input_request_id") or "")
        if request_id:
            task = self._append_human_response(task, request_id)
        return ContinuationPlan(
            task=task,
            workspace=workspace or checkpoint_resume_workspace(checkpoint),
            human_thread_id=thread_id,
        )

    def _append_human_response(self, task: str, request_id: str) -> str:
        request = self.human_inputs.get(request_id)
        if request is None:
            raise ValueError(f"human input request not found: {request_id}")
        if request.status == "pending":
            raise ValueError(f"human input is still pending: {request_id}")
        if request.status == "cancelled":
            raise ValueError(f"human input request was cancelled: {request_id}")
        return "\n".join(
            [
                task,
                "",
                "Human response from the previous run:",
                f"Question: {request.question}",
                f"Answer: {request.answer}",
                "Continue from this explicit operator input; do not ask the same question again.",
            ]
        )


def checkpoint_resume_workspace(checkpoint: TaskCheckpoint) -> str:
    """临时 worktree 的恢复默认回到原始 checkout。"""

    metadata = checkpoint.metadata if isinstance(checkpoint.metadata, dict) else {}
    environment = metadata.get("execution_environment")
    if isinstance(environment, dict) and environment.get("requested_workspace"):
        return str(environment["requested_workspace"])
    return checkpoint.workspace
