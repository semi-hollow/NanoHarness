"""把 LIVE coordination 接入现有 Tool 与 RunControl 的薄 Adapter。

系统角色：向有合法 LIVE route 的 Worker 暴露一个 publish Tool，并把它的 mailbox
投影成 ``AgentLoop`` 已有的 Runtime coordination signal。
输入：worker-bound ``LiveWorkerContextPort``。
输出：Tool Observation 或 ``RuntimeCoordinationSignal``。
边界：不保存 mailbox、不校验全局版本、不把 coordination 伪装成 operator steer。

折叠导航：1 Publish Tool；2 RunControl mailbox Adapter。
"""

from __future__ import annotations

import json

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.domain.run_control import (
    RunControlSignal,
    RuntimeCoordinationSignal,
)
from agent_forge.runtime.ports.run_control import RunControlPort
from agent_forge.tools.base import Tool

from ..ports import LiveWorkerContextPort


# region 1. Publish Tool：模型只填写语义字段，publisher/generation/attempt 由 Runtime 注入
class PublishHandoffEventTool(Tool):
    """发布 Runtime 授权的 READY、FEEDBACK 或 UPDATE；模型不能填写身份。"""

    name = "publish_handoff_event"
    description = (
        "Publish bounded semantic READY, FEEDBACK, or UPDATE evidence on an "
        "authorized LIVE route. This never shares private conversation, worktree, or diff."
    )

    def __init__(self, context: LiveWorkerContextPort) -> None:
        self._context = context

    def schema(self) -> ToolSchema:
        """返回不包含 publisher identity 的最小模型可见参数。"""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                "event_type": "str",
                "target_task_id": "str",
                "semantic_key": "str",
                "version": "int",
                "summary": "str",
                "evidence": "list[str]",
                "caused_by_event_id": "str",
            },
            "required": [
                "event_type",
                "target_task_id",
                "semantic_key",
                "version",
                "summary",
                "evidence",
            ],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        """做物理参数检查后调用 worker-bound context；所有语义规则仍由 Runtime 拥有。"""

        # region 1. Tool 参数边界：先拒绝错误 evidence/version 物理类型
        evidence = arguments.get("evidence")
        if not isinstance(evidence, list):
            return Observation(self.name, False, "evidence must be a list of strings")
        version = arguments.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            return Observation(self.name, False, "version must be an integer")
        # endregion 1. Tool 参数边界结束

        # region 2. Runtime 授权：route、type、version、cause 任一失败都返回 rejected Observation
        try:
            event = self._context.publish(
                event_type=str(arguments.get("event_type") or ""),
                target_task_id=str(arguments.get("target_task_id") or ""),
                semantic_key=str(arguments.get("semantic_key") or ""),
                version=version,
                summary=str(arguments.get("summary") or ""),
                evidence=[str(item) for item in evidence],
                caused_by_event_id=str(arguments.get("caused_by_event_id") or ""),
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            return Observation(self.name, False, f"coordination rejected: {exc}")
        # endregion 2. Runtime 授权结束

        # region 3. 成功 Observation：只返回可审计 identity，不回传其他 Worker 私有状态
        return Observation(
            self.name,
            True,
            json.dumps(
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type.value,
                    "version": event.version,
                    "target_task_id": event.target_task_id,
                    "human_authority": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        # endregion 3. 成功 Observation 结束
# endregion 1. Publish Tool 结束


# region 2. RunControl mailbox Adapter：只实现 coordination，人工 terminal/steer 永远为空
class LiveHandoffRunControl(RunControlPort):
    """把 Worker mailbox 投影到现有 AgentLoop 模型安全边界。"""

    def __init__(self, context: LiveWorkerContextPort) -> None:
        self._context = context

    def take_terminal(self, run_id: str) -> RunControlSignal | None:
        return None

    def drain_steers(self, run_id: str) -> list[RunControlSignal]:
        return []

    def drain_coordination(
        self,
        run_id: str,
        *,
        boundary: str,
    ) -> list[RuntimeCoordinationSignal]:
        return [
            RuntimeCoordinationSignal(
                event_id=event.event_id,
                content=json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True),
                plan_generation_id=event.plan_generation_id,
                worker_attempt_id=event.worker_attempt_id,
                publisher_task_id=event.publisher_task_id,
                target_task_id=event.target_task_id,
                event_type=event.event_type.value,
                semantic_key=event.semantic_key,
                version=event.version,
                human_authority=False,
            )
            for event in self._context.drain_mailbox(boundary=boundary)
        ]
# endregion 2. RunControl mailbox Adapter 结束
