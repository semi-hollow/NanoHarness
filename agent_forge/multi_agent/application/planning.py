"""Planner 只提出 Single/Fanout 方案；Runtime 负责校验和执行。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_forge.atomic_json import atomic_write_json
from agent_forge.context.repo_map import build_repo_map
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.ports.model import ModelPort
from agent_forge.runtime.structured_output import StructuredOutputParser

from ..domain.live import FanoutPlan, LiveSubagentResult, WorkerHandoff
from ..domain.planning import PlanningDecision

MAX_REPO_MAP_CHARS = 12_000

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
    },
}


class PlanningError(RuntimeError):
    """Planner 在一次修复后仍未产生可执行方案。"""


@dataclass(frozen=True)
class PlanningOutcome:
    """Adaptive gate 的决定或明确 Single fallback 证据。"""

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
            # Raw output is deliberately omitted from the durable artifact. The parsed
            # contract and failure are sufficient evidence without copying model prose.
        }


class AdaptivePlanner:
    """一次提议、至多一次 JSON 修复的有界 Planner/Replanner。"""

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
        """从自然语言任务产生受校验的 Single/Fanout 决定。"""

        repo_map = build_repo_map(workspace)[:MAX_REPO_MAP_CHARS]
        prompt = "\n".join(
            [
                "Choose the smallest safe execution strategy for this repository task.",
                "Use mode=single for local or highly coupled work. Use fanout only when "
                "tasks have useful isolation or dependencies.",
                "For fanout, propose coarse relative write scopes and only listed tools.",
                f"Maximum fanout tasks: {self.max_fanout_tasks}",
                f"Maximum steps per task: {self.max_steps}",
                f"Available tools: {self.available_tools}",
                f"Original task: {task}",
                "Bounded repository map:",
                repo_map or "(empty repository map)",
            ]
        )
        return self._request(prompt, validate_fanout_goal=task)

    def replan(
        self,
        *,
        goal: str,
        current_plan: FanoutPlan,
        completed_handoffs: list[WorkerHandoff],
        failed_results: list[LiveSubagentResult],
    ) -> PlanningDecision:
        """仅替换未完成工作；completed history 的冻结由 Coordinator 校验。"""

        prompt = "\n".join(
            [
                "Replan only the unfinished part of this fanout run.",
                "Return mode=fanout and tasks for remaining work only.",
                "Completed task IDs may be dependencies but must not be redefined.",
                f"Maximum remaining tasks: {self.max_fanout_tasks}",
                f"Available tools: {self.available_tools}",
                f"Goal: {goal}",
                "Current plan:",
                _bounded_json(current_plan.to_dict()),
                "Frozen completed handoffs:",
                _bounded_json([handoff.to_dict() for handoff in completed_handoffs]),
                "Failed remaining results:",
                _bounded_json([_failure_evidence(result) for result in failed_results]),
            ]
        )
        outcome = self._request(prompt)
        if outcome.decision is None or outcome.decision.mode != "fanout":
            raise PlanningError(outcome.failure or "replanner did not return fanout")
        return outcome.decision

    def _request(
        self,
        prompt: str,
        *,
        validate_fanout_goal: str = "",
    ) -> PlanningOutcome:
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
        for attempt in range(2):
            response = model.chat(messages, [])
            if response.error:
                failure = f"provider failure: {response.error}"
                break
            raw = response.content or ""
            raw_responses.append(raw)
            parsed = parser.parse(raw)
            if parsed.ok:
                try:
                    decision = PlanningDecision.from_mapping(
                        parsed.data,
                        available_tools=self.available_tools,
                        max_fanout_tasks=self.max_fanout_tasks,
                    )
                    if validate_fanout_goal and decision.mode == "fanout":
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
            if attempt == 0:
                messages.append(Message(role="assistant", content=raw))
                messages.append(Message(role="user", content=repair_prompt))
        return PlanningOutcome(
            decision=None,
            fallback_to_single=True,
            failure=failure or "planner produced no valid decision",
            attempts=len(raw_responses) or 1,
            raw_responses=raw_responses,
        )


def write_planning_artifact(path: str | Path, outcome: PlanningOutcome) -> Path:
    artifact = Path(path)
    atomic_write_json(artifact, outcome.to_dict())
    return artifact


def resumed_planning_outcome(plan: FanoutPlan) -> PlanningOutcome:
    """Resume 的 route 证据；不会重新调用 Planner。"""

    decision = PlanningDecision.from_mapping(
        {
            "mode": "fanout",
            "reason": "resume existing validated fanout plan",
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
        },
        available_tools={tool for task in plan.tasks for tool in task.allowed_tools},
    )
    return PlanningOutcome(
        decision=decision,
        fallback_to_single=False,
        attempts=0,
        source="resume",
    )


def _bounded_json(value: Any, limit: int = 8_000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:limit]


def _failure_evidence(result: LiveSubagentResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "status": result.status,
        "attempt": result.attempt,
        "failure_kind": result.failure_kind,
        "retryable": result.retryable,
        "error": result.error[:1000],
        "handoff": result.handoff.to_dict() if result.handoff else None,
    }
