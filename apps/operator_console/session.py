"""Operator Console 与 Harness 之间唯一的应用层桥梁。

核心入口是 ``start``、``answer_and_resume``、``decide_and_resume`` 和
``resume``；路径查找、pending request 选择等方法负责展示层准备工作。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Literal

from agent_forge.control import RunController
from agent_forge.harness import Harness, RunRequest, RunResult
from apps.operator_console.application import ConversationThreadLibrary
from agent_forge.runtime.application.operator_control import (
    DecideApproval,
    RespondToHumanInput,
)
from agent_forge.runtime.domain.approval import ApprovalRequest
from agent_forge.runtime.domain.human_input import HumanInputRequest
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus
from agent_forge.runtime.ports import ApprovalRepository, HumanInputRepository
from agent_forge.runtime.api import latest_checkpoint_path, load_task_checkpoint
from agent_forge.infrastructure.storage_layout import INDEX_ROOT


@dataclass(frozen=True)
class OperatorPrompt:
    """操作台需要展示的一次人工问题或状态变更操作审批。"""

    kind: Literal["human_input", "approval"]
    key: str
    title: str
    body: str
    choices: tuple[str, ...] = ()
    details: str = ""


class OperatorSession:
    """持有一次前台会话的 Harness、控制端口和 durable 控制仓储。

    Textual 只调用本类，不直接读写 JSON。一次 continuation 会创建新的 Runtime
    run，但本对象保持不变，所以用户看到的是连续操作体验。
    """

    def __init__(
        self,
        *,
        harness: Harness,
        request: RunRequest,
        controller: RunController,
        approvals: ApprovalRepository,
        human_inputs: HumanInputRepository,
        thread_library: ConversationThreadLibrary,
        thread_id: str,
    ) -> None:
        self.harness = harness
        self.request = request
        self.controller = controller
        self.approvals = approvals
        self.human_inputs = human_inputs
        self.thread_library = thread_library
        self.thread_id = thread_id
        self._lock = RLock()
        self._result: RunResult | None = None
        self._checkpoint: TaskCheckpoint | None = None
        self._checkpoint_path: Path | None = None
        self._artifact_dir: Path | None = None

    @property
    def result(self) -> RunResult | None:
        with self._lock:
            return self._result

    @property
    def checkpoint(self) -> TaskCheckpoint | None:
        with self._lock:
            return self._checkpoint

    @property
    def artifact_dir(self) -> Path | None:
        with self._lock:
            return self._artifact_dir

    # 主要入口：从界面输入启动真实 Harness 主链。
    def start(self) -> RunResult:
        """执行 ``Harness.run``，并保存后续人工控制所需的当前状态。"""

        return self._remember_result(
            self.harness.run(self.request),
        )

    # 主要入口：保存人工答案，并立即从当前 durable checkpoint 续跑。
    def answer_and_resume(self, answer: str) -> RunResult:
        """完成 ``waiting_human -> responded -> same-Turn resume``。"""

        prompt = self.require_prompt("human_input")
        RespondToHumanInput(self.human_inputs).respond(
            prompt.key,
            answer=answer,
        )
        return self.resume()

    # 主要入口：保存工具审批决定，并立即从当前 durable checkpoint 续跑。
    def decide_and_resume(
        self,
        decision: Literal["approved", "rejected"],
        *,
        note: str = "",
    ) -> RunResult:
        """完成 ``waiting_approval -> decision -> same-Turn resume``。"""

        prompt = self.require_prompt("approval")
        DecideApproval(self.approvals).decide(
            prompt.key,
            decision,
            note=note,
        )
        return self.resume()

    # 主要入口：从 checkpoint 为同一 unfinished Turn 创建新 Run。
    def resume(self) -> RunResult:
        """恢复可见任务状态；不声称恢复模型栈、KV Cache 或进程现场。"""

        self._require_checkpoint()
        return self._resume()

    # 主要入口：终态后接收新要求，在同一 ConversationThread 下创建新 Turn/Run。
    def continue_with_user_message(self, message: str) -> RunResult:
        """向同一 Thread 追加权威用户输入，并创建全新的 Turn/Run。"""

        user_message = message.strip()
        if not user_message:
            raise ValueError("follow-up message must not be empty")
        checkpoint = self._require_checkpoint()
        if checkpoint.status in {
            TaskRunStatus.WAITING_APPROVAL.value,
            TaskRunStatus.WAITING_HUMAN.value,
            TaskRunStatus.PAUSED.value,
            TaskRunStatus.RUNNING.value,
        }:
            raise RuntimeError(
                f"当前状态 {checkpoint.status} 不能创建后续 Run；请先完成或恢复当前 Run"
            )
        follow_up_request = replace(
            self.request,
            task=user_message,
            thread_id=self.thread_id,
            turn_id="",
            context_revision=0,
            resume_state="",
            resume_execution_workspace="",
            run_label="follow-up",
        )
        return self._remember_result(self.harness.run(follow_up_request))

    def attach_run(self, run_dir: str | Path) -> TaskCheckpoint:
        """载入已有 run 的最新 checkpoint，供进程重启后继续操作。"""

        artifact_dir = Path(run_dir).expanduser().resolve()
        checkpoint_path = Path(latest_checkpoint_path(str(artifact_dir)))
        checkpoint = load_task_checkpoint(str(checkpoint_path))
        if checkpoint.thread_id != self.thread_id:
            raise ValueError("checkpoint does not belong to selected ConversationThread")
        with self._lock:
            self._result = None
            self._checkpoint = checkpoint
            self._checkpoint_path = checkpoint_path
            self._artifact_dir = artifact_dir
        return checkpoint

    def attach_latest(self, workspace: str | Path) -> TaskCheckpoint:
        """读取 workspace 的 latest 指针并接管最近一次 run。"""

        pointer = Path(workspace).expanduser().resolve() / INDEX_ROOT / "run.txt"
        if not pointer.is_file():
            raise FileNotFoundError(f"没有可恢复的 latest run: {pointer}")
        raw_target = Path(pointer.read_text(encoding="utf-8").strip())
        target = raw_target if raw_target.is_absolute() else pointer.parent / raw_target
        return self.attach_run(target)

    def pending_prompt(self) -> OperatorPrompt | None:
        """从当前 checkpoint 解析真正待处理的 human/approval 记录。"""

        checkpoint = self.checkpoint
        if checkpoint is None:
            return None
        if checkpoint.status == TaskRunStatus.WAITING_HUMAN.value:
            human_request = self._pending_human_input(checkpoint)
            return (
                self._human_prompt(human_request) if human_request is not None else None
            )
        if checkpoint.status == TaskRunStatus.WAITING_APPROVAL.value:
            approval_request = self._pending_approval(checkpoint)
            return (
                self._approval_prompt(approval_request)
                if approval_request is not None
                else None
            )
        return None

    def require_prompt(
        self,
        kind: Literal["human_input", "approval"],
    ) -> OperatorPrompt:
        """返回当前 prompt，并拒绝界面把输入写给错误状态。"""

        prompt = self.pending_prompt()
        if prompt is None or prompt.kind != kind:
            raise RuntimeError(f"当前没有待处理的 {kind}")
        return prompt

    def pause(self) -> None:
        """请求 Runtime 在下一个安全边界暂停。"""

        self.controller.pause()

    def cancel(self) -> None:
        """请求 Runtime 在下一个安全边界取消；不回滚已经发生的状态变更。"""

        self.controller.cancel()

    def steer(self, message: str) -> None:
        """把新的用户方向注入下一次模型调用。"""

        if not message.strip():
            raise ValueError("steer message must not be empty")
        self.controller.steer(message.strip())

    def _remember_result(
        self,
        result: RunResult,
    ) -> RunResult:
        if result.thread_id != self.thread_id:
            raise ValueError("RunResult moved to a different ConversationThread")
        checkpoint_path = (
            result.artifact_dir / "task_state" / f"{result.checkpoint.run_id}.json"
        )
        if not checkpoint_path.is_file():
            checkpoint_path = Path(latest_checkpoint_path(str(result.artifact_dir)))
        with self._lock:
            self._result = result
            self._checkpoint = result.checkpoint
            self._checkpoint_path = checkpoint_path
            self._artifact_dir = result.artifact_dir
        return result

    def _require_checkpoint_path(self) -> Path:
        with self._lock:
            checkpoint_path = self._checkpoint_path
        if checkpoint_path is None:
            raise RuntimeError("当前会话还没有可恢复 checkpoint")
        return checkpoint_path

    def _require_checkpoint(self) -> TaskCheckpoint:
        checkpoint = self.checkpoint
        if checkpoint is None:
            raise RuntimeError("当前会话还没有可恢复 checkpoint")
        return checkpoint

    def _resume(self) -> RunResult:
        checkpoint_path = self._require_checkpoint_path()
        return self._remember_result(self.harness.resume(checkpoint_path))

    def _pending_human_input(
        self,
        checkpoint: TaskCheckpoint,
    ) -> HumanInputRequest | None:
        request_id = str(checkpoint.metadata.get("human_input_request_id") or "")
        if request_id:
            human_input_request = self.human_inputs.get(request_id)
            if (
                human_input_request is not None
                and human_input_request.status == "pending"
            ):
                return human_input_request
        for pending_request in self.human_inputs.list_pending():
            if pending_request.run_id == checkpoint.run_id:
                return pending_request
        return None

    def _pending_approval(
        self,
        checkpoint: TaskCheckpoint,
    ) -> ApprovalRequest | None:
        for pending_approval in self.approvals.list_pending():
            belongs_to_same_run = pending_approval.run_id == checkpoint.run_id
            matches_interrupted_tool = (
                pending_approval.workspace == checkpoint.workspace
                and pending_approval.tool_name == checkpoint.last_tool
            )
            if belongs_to_same_run or matches_interrupted_tool:
                return pending_approval
        return None

    @staticmethod
    def _human_prompt(human_input_request: HumanInputRequest) -> OperatorPrompt:
        return OperatorPrompt(
            kind="human_input",
            key=human_input_request.request_id,
            title="Agent 需要你的补充信息",
            body=human_input_request.question,
            choices=tuple(human_input_request.choices),
            details=human_input_request.reason,
        )

    @staticmethod
    def _approval_prompt(approval_request: ApprovalRequest) -> OperatorPrompt:
        details = json.dumps(
            {
                "tool": approval_request.tool_name,
                "action": approval_request.action,
                "command": approval_request.command,
                "arguments": approval_request.arguments,
                "workspace": approval_request.workspace,
                "reason": approval_request.reason,
                "fingerprint": approval_request.operation_fingerprint,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return OperatorPrompt(
            kind="approval",
            key=approval_request.operation_key,
            title=f"审批状态变更操作：{approval_request.tool_name}",
            body=approval_request.reason or "该工具调用需要人工授权。",
            details=details,
        )
