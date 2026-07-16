"""Runtime 控制状态的持久化端口。"""

from __future__ import annotations

from typing import Any, Protocol

from agent_forge.contracts import JsonObject
from agent_forge.runtime.domain.approval import ApprovalRequest
from agent_forge.runtime.domain.human_input import HumanInputRequest
from agent_forge.runtime.domain.operation import OperationRecord
from agent_forge.runtime.domain.task import TaskCheckpoint


class TaskStateRepository(Protocol):
    """Checkpoint 的创建、转换和恢复接口。"""

    def start(
        self,
        run_id: str,
        task: str,
        workspace: str,
        agent_name: str,
        metadata: JsonObject | None = None,
    ) -> TaskCheckpoint:
        """创建初始 checkpoint。"""

    def update(
        self,
        checkpoint: TaskCheckpoint,
        *,
        status: str | None = None,
        current_step: int | None = None,
        last_tool: str | None = None,
        last_observation: str | None = None,
        stop_reason: str | None = None,
        final_answer: str | None = None,
        resume_hint: str | None = None,
        messages_count: int | None = None,
        observations_count: int | None = None,
        context_digest: JsonObject | None = None,
        metadata: JsonObject | None = None,
        updated_at: float | None = None,
    ) -> TaskCheckpoint:
        """应用并持久化一次 checkpoint 转换。"""

    def load_path(self, path: str) -> TaskCheckpoint:
        """从显式路径加载 checkpoint。"""


class HumanInputRepository(Protocol):
    """非阻塞人工问题队列。"""

    def request(
        self,
        *,
        thread_id: str,
        kind: str,
        question: str,
        choices: list[str] | None,
        workspace: str,
        run_id: str,
        step: int,
        agent_name: str,
        reason: str,
    ) -> HumanInputRequest:
        """创建或读取幂等人工问题。"""

    def get(self, request_id: str) -> HumanInputRequest | None:
        """按 request id 查询人工问题。"""

    def respond(
        self,
        request_id: str,
        answer: str,
        note: str = "",
    ) -> HumanInputRequest:
        """保存人工回答。"""

    def cancel(self, request_id: str, note: str = "") -> HumanInputRequest:
        """取消仍未回答的问题。"""


class ApprovalRepository(Protocol):
    """副作用审批记录。"""

    def get(self, operation_key: str) -> ApprovalRequest | None:
        """按 operation key 查询审批。"""

    def request(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        action: str,
        command: str,
        workspace: str,
        run_id: str,
        step: int,
        agent_name: str,
        reason: str,
        operation_fingerprint: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """创建或读取一个审批请求。"""

    def mark_stale(
        self,
        operation_key: str,
        note: str = "",
    ) -> ApprovalRequest:
        """使目标已变化的审批失效。"""

    def decide(
        self,
        operation_key: str,
        status: str,
        note: str = "",
    ) -> ApprovalRequest:
        """保存操作员的批准或拒绝。"""


class OperationLedgerRepository(Protocol):
    """副作用操作的幂等账本。"""

    def operation_key(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        workspace: str,
        action: str = "",
    ) -> str:
        """构造稳定操作标识。"""

    def operation_fingerprint(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        workspace: str,
        action: str = "",
    ) -> dict[str, Any]:
        """读取目标当前状态的最小指纹。"""

    def get(self, operation_key: str) -> OperationRecord | None:
        """读取现有账本记录。"""

    def ensure_planned(
        self,
        operation_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        action: str,
        workspace: str,
        *,
        run_id: str,
        step: int,
        status: str = "planned",
        pre_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        """创建或恢复 planned 记录。"""

    def record_pending(
        self,
        operation_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        action: str,
        workspace: str,
        *,
        run_id: str,
        step: int,
        pre_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        """记录等待审批。"""

    def record_approved(
        self,
        operation_key: str,
        *,
        run_id: str,
        step: int,
    ) -> OperationRecord:
        """记录已授权。"""

    def record_executed(
        self,
        operation_key: str,
        *,
        run_id: str,
        step: int,
        observation: str,
        post_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        """记录成功执行。"""

    def record_failed(
        self,
        operation_key: str,
        *,
        run_id: str,
        step: int,
        observation: str,
        post_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        """记录失败执行。"""
