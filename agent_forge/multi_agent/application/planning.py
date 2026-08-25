"""自然语言任务到唯一 ``FanoutPlan`` 之前的有界提议层。

系统角色：让模型选择 Single/Multi 并提出结构化任务图，但不授予任何执行权。
输入：原始任务、bounded repository map 和可用工具。
输出：一次 ``PlanningOutcome``。
相邻边界：``StructuredOutputParser`` 解析 JSON；Planner 至多发起一次结构或领域约束修复；
``FanoutPlan`` 再校验 HARD/LIVE 图；``FanoutCoordinator`` 才执行。

折叠导航：1 输出契约；2 Planner 主链；3 artifact/resume；4 纯投影 helper。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.context.adapters.repository_map import build_repo_map
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.ports.model import ModelPort
from agent_forge.runtime.domain.structured_output import StructuredOutputParser

from ..domain.fanout import FanoutPlan
from ..domain.planning import PlanningDecision

MAX_REPO_MAP_CHARS = 12_000

# region 1. 输出契约：固定 Schema、明确失败类型，并且持久化时不保存模型原文
PLANNING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["mode", "reason", "global_acceptance_criteria", "tasks"],
    "properties": {
        "mode": {"type": "string"},
        "reason": {"type": "string"},
        "global_acceptance_criteria": {
            "type": "array",
            "items": {"type": "string"},
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "task",
                    "depends_on",
                    "write_scope",
                    "allowed_tools",
                    "acceptance_criteria",
                    "max_steps",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "task": {"type": "string"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "write_scope": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "max_steps": {"type": "integer"},
                },
            },
        },
        "live_dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "producer_task_id",
                    "target_task_id",
                    "semantic_key",
                ],
                "properties": {
                    "producer_task_id": {"type": "string"},
                    "target_task_id": {"type": "string"},
                    "semantic_key": {"type": "string"},
                },
            },
        },
    },
}


class PlanningError(RuntimeError):
    """Planner 在一次修复后仍未产生可执行方案。"""


@dataclass(frozen=True)
class PlanningOutcome:
    """Ultra planning gate 的决定或明确 Single fallback 证据。"""

    decision: PlanningDecision | None
    fallback_to_single: bool
    failure: str = ""
    attempts: int = 0
    source: str = "planner"
    raw_responses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": self.source,
            "fallback_to_single": self.fallback_to_single,
            "failure": self.failure,
            "attempts": self.attempts,
            "decision": self.decision.to_dict() if self.decision else None,
            # 持久化产物不复制模型原文；已解析契约和失败原因足以构成审计证据。
        }
# endregion 1. 输出契约结束


# region 2. Planner 主链：一次正常请求 + 至多一次结构/领域修复，不直接启动 Worker
class AdaptivePlanner:
    """一次提议、至多一次结构或领域约束修复的有界 Planner。"""

    # region 1. Public 决策：Planner 在 Fanout Run 之前只执行一次
    def __init__(
        self,
        *,
        model_factory: Callable[[], ModelPort],
        available_tools: list[str],
        max_fanout_tasks: int = 16,
        max_steps: int = 12,
    ) -> None:
        self.model_factory = model_factory
        self.available_tools = sorted(set(available_tools))
        self.max_fanout_tasks = max(1, min(int(max_fanout_tasks), 16))
        self.max_steps = max(2, min(int(max_steps), 32))

    def decide(self, task: str, workspace: str | Path) -> PlanningOutcome:
        """从自然语言任务产生受校验的 Single/Multi 决定。"""

        repo_map = build_repo_map(workspace)[:MAX_REPO_MAP_CHARS]
        prompt = "\n".join(
            [
                "Choose the smallest safe execution strategy for this repository task.",
                "Use mode=single for local or highly coupled work. Use multi only when "
                "tasks have useful isolation or dependencies.",
                "For multi, propose coarse relative write scopes and only listed tools.",
                "Use depends_on only for HARD file/result dependencies. Use "
                "live_dependencies only when semantic READY/FEEDBACK/UPDATE can let "
                "a downstream worker start early without seeing unmerged files.",
                f"Maximum multi-agent tasks: {self.max_fanout_tasks}",
                f"Maximum steps per task: {self.max_steps}",
                f"Available tools: {self.available_tools}",
                f"Original task: {task}",
                "Bounded repository map:",
                repo_map or "(empty repository map)",
            ]
        )
        outcome = self._request(prompt, validate_fanout_goal=task)
        # Ultra 中的单任务 Multi 提议没有 fanout 价值；Runtime 将其规范化为
        # Single，上层因而复用同一条 canonical AgentLoop，不创建 Coordinator。
        if (
            outcome.decision is not None
            and outcome.decision.mode == "multi"
            and len(outcome.decision.tasks) == 1
        ):
            decision = PlanningDecision(
                mode="single",
                reason=(
                    "one effective task; canonical Single selected. "
                    + outcome.decision.reason
                ),
                global_acceptance_criteria=(
                    outcome.decision.global_acceptance_criteria
                ),
            )
            return PlanningOutcome(
                decision=decision,
                fallback_to_single=False,
                attempts=outcome.attempts,
                source=outcome.source,
                raw_responses=outcome.raw_responses,
            )
        return outcome

    # endregion 1. Public 决策结束

    # region 2. 模型请求：解析、Domain 校验、一次 repair，失败则显式 fallback
    def _request(
        self,
        prompt: str,
        *,
        validate_fanout_goal: str = "",
    ) -> PlanningOutcome:
        """请求一次规划并最多修复一次结构，任何失败都显式回退而不执行半成品。

        伪代码：固定 Schema/Prompt -> 请求模型 -> Parser 校验 JSON -> Domain 校验字段
        -> 可选 FanoutPlan 图校验 -> 失败时追加一次 repair -> 仍失败则 fallback_to_single。
        """

        # region 1. 固定请求：执行前 structured planning，失败时最多一次 bounded repair
        parser = StructuredOutputParser(PLANNING_SCHEMA, max_repair_attempts=1)
        model = self.model_factory()
        messages = [
            Message(
                role="system",
                content=(
                    "You are NanoHarness Planner. You may propose work, but the Runtime "
                    "will validate every field. " + parser.json_instructions()
                ),
            ),
            Message(role="user", content=prompt),
        ]
        raw_responses: list[str] = []
        failure = ""
        # endregion 1. 固定请求结束

        # region 2. 有界解析：模型只提议，Parser 与 Domain 两次检查后才形成 decision
        # 最多两轮：第一次正常回答，第二次只修复第一次的结构/领域错误。
        for attempt in range(2):
            response = model.chat(messages, [])
            # Provider 失败不是结构错误，不做 repair，直接保留失败事实。
            if response.error:
                failure = f"provider failure: {response.error}"
                break
            raw = response.content or ""
            raw_responses.append(raw)
            parsed = parser.parse(raw)
            # 只有 Schema 通过才进入 Domain contract；否则使用 Parser 的 repair prompt。
            if parsed.ok:
                # Domain 校验失败也只允许同一次 repair，模型无法绕过工具和任务上限。
                try:
                    decision = PlanningDecision.from_mapping(
                        parsed.data,
                        available_tools=self.available_tools,
                        max_fanout_tasks=self.max_fanout_tasks,
                    )
                    # 初次 decide 的 multi 还要立即构造 FanoutPlan，提前验证组合依赖图。
                    if validate_fanout_goal and decision.mode == "multi":
                        decision.to_fanout_plan(validate_fanout_goal)
                    return PlanningOutcome(
                        decision=decision,
                        fallback_to_single=False,
                        attempts=attempt + 1,
                        raw_responses=raw_responses,
                    )
                except ValueError as exc:
                    failure = str(exc)
                    repair_prompt = parser.build_repair_prompt(raw, failure)
            else:
                failure = parsed.error
                repair_prompt = parsed.repair_prompt
            # 只有第一次失败才追加 assistant 原文和定向 repair；第二次失败直接收口。
            if attempt == 0:
                messages.append(Message(role="assistant", content=raw))
                messages.append(Message(role="user", content=repair_prompt))
        # endregion 2. 有界解析结束

        # region 3. 失败收口：不执行半结构计划，交给上层选择 Single fallback
        return PlanningOutcome(
            decision=None,
            fallback_to_single=True,
            failure=failure or "planner produced no valid decision",
            attempts=len(raw_responses) or 1,
            raw_responses=raw_responses,
        )
        # endregion 3. 失败收口结束
    # endregion 2. 模型请求结束
# endregion 2. Planner 主链结束


# region 3. Artifact 与 resume：保存已解析事实，恢复时绝不重新调用 Planner
def write_planning_artifact(path: str | Path, outcome: PlanningOutcome) -> Path:
    """原子写入已解析 PlanningOutcome，不把 raw model response 复制到 artifact。"""

    artifact = Path(path)
    atomic_write_json(artifact, outcome.to_dict())
    return artifact


def resumed_planning_outcome(plan: FanoutPlan) -> PlanningOutcome:
    """Resume 的 route 证据；不会重新调用 Planner。"""

    decision = PlanningDecision.from_mapping(
        {
            "mode": "multi",
            "reason": "resume existing validated multi-agent plan",
            "global_acceptance_criteria": plan.global_acceptance_criteria,
            "tasks": [
                {
                    "id": task.id,
                    "task": task.task,
                    "depends_on": task.depends_on,
                    "write_scope": task.write_scope,
                    "allowed_tools": task.allowed_tools,
                    "acceptance_criteria": task.acceptance_criteria,
                    "max_steps": task.max_steps,
                }
                for task in plan.tasks
            ],
            "live_dependencies": [
                dependency.to_dict() for dependency in plan.live_dependencies
            ],
        },
        available_tools={tool for task in plan.tasks for tool in task.allowed_tools},
    )
    return PlanningOutcome(
        decision=decision,
        fallback_to_single=False,
        attempts=0,
        source="resume",
    )
# endregion 3. Artifact 与 resume 结束
