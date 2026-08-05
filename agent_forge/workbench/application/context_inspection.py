from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContextComponent:
    """模型输入中的一个可计量区段。"""

    key: str
    chars: int


@dataclass(frozen=True)
class ToolDecision:
    """一次模型工具决定，以及 Runtime 执行后返回的可观测反馈。"""

    tool_name: str
    target: str
    arguments: dict[str, Any]
    succeeded: bool | None
    feedback: str


@dataclass(frozen=True)
class ContextTurnInspection:
    """供 Workbench 展示的一轮 Context -> Decision -> Feedback 快照。"""

    step: int
    phase: str
    phase_reason: str
    is_key_turn: bool
    key_reason: str
    previous_evidence: tuple[str, ...]
    message_count: int
    message_delta: int
    estimated_tokens: int
    token_delta: int
    hard_input_limit: int
    compacted: bool
    compaction_reason: str
    total_context_chars: int
    max_context_chars: int
    truncated: bool
    input_components: tuple[ContextComponent, ...]
    system_sections: tuple[ContextComponent, ...]
    visible_tools: tuple[str, ...]
    dropped_tools: tuple[str, ...]
    tools_changed: bool
    active_skills: tuple[str, ...]
    skills_changed: bool
    selected_files: tuple[str, ...]
    files_seen: tuple[str, ...]
    working_memory_summary: str
    model_name: str
    model_response_summary: str
    reasoning_tokens: int
    estimated_cost_usd: float
    tool_decisions: tuple[ToolDecision, ...]


# 主要入口：把细粒度 Trace 投影为面向学习者的逐轮上下文视图。
def build_context_turn_inspections(
    trace: dict[str, Any],
) -> tuple[ContextTurnInspection, ...]:
    """按 Turn 聚合上下文、模型决定和工具反馈，不推断隐藏思维链。"""

    events = [
        event
        for event in trace.get("events") or []
        if isinstance(event, dict) and int(event.get("step") or 0) > 0
    ]
    events_by_step: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        events_by_step.setdefault(int(event.get("step") or 0), []).append(event)

    turns: list[ContextTurnInspection] = []
    previous_evidence: tuple[str, ...] = (
        "初始 Task、Runtime 指令与权限边界进入第一轮。",
    )
    previous_messages = 0
    previous_tokens = 0
    previous_tools: tuple[str, ...] = ()
    previous_skills: tuple[str, ...] = ()
    files_seen: list[str] = []
    saw_validation_failure = False
    saw_write = False
    saw_validation_pass = False

    for step in sorted(events_by_step):
        step_events = events_by_step[step]
        context = _event_payload(step_events, "context_assembly", "context")
        window = _event_payload(step_events, "context_window", "context_window")
        model_request = _event_payload(step_events, "model_started", "model_request")
        llm_call = _last_event(step_events, "llm_call")
        tool_decisions = _build_tool_decisions(step_events)

        visible_tools = tuple(str(value) for value in context.get("available_tools") or [])
        active_skills = tuple(str(value) for value in context.get("active_skills") or [])
        tool_routing = _mapping(context.get("tool_routing"))
        dropped_tools = tuple(str(value) for value in tool_routing.get("dropped_tools") or [])
        selected_files = tuple(
            _short_path(str(value)) for value in context.get("selected_files") or []
        )
        input_breakdown = _mapping(llm_call.get("llm_input_breakdown_chars"))
        input_components = tuple(
            ContextComponent(key=key, chars=int(input_breakdown.get(key) or 0))
            for key in ("system_context", "conversation_history", "tool_schemas")
        )
        budget_breakdown = _mapping(context.get("budget_breakdown"))
        system_sections = tuple(
            ContextComponent(key=str(key), chars=int(value or 0))
            for key, value in sorted(
                budget_breakdown.items(),
                key=lambda item: int(item[1] or 0),
                reverse=True,
            )
        )

        message_count = int(model_request.get("messages_count") or 0)
        estimated_tokens = int(
            window.get("estimated_tokens_after")
            or model_request.get("estimated_prompt_tokens")
            or 0
        )
        phase, phase_reason = _classify_turn(tool_decisions)
        key_reason = _key_turn_reason(
            step=step,
            tool_decisions=tool_decisions,
            compacted=window.get("compacted") is True,
            saw_validation_failure=saw_validation_failure,
            saw_write=saw_write,
            saw_validation_pass=saw_validation_pass,
        )
        model_usage = _mapping(llm_call.get("model_usage"))

        turns.append(
            ContextTurnInspection(
                step=step,
                phase=phase,
                phase_reason=phase_reason,
                is_key_turn=bool(key_reason),
                key_reason=key_reason,
                previous_evidence=previous_evidence,
                message_count=message_count,
                message_delta=message_count - previous_messages,
                estimated_tokens=estimated_tokens,
                token_delta=estimated_tokens - previous_tokens,
                hard_input_limit=int(window.get("hard_input_limit") or 0),
                compacted=window.get("compacted") is True,
                compaction_reason=str(window.get("reason") or "未记录"),
                total_context_chars=int(context.get("total_chars") or 0),
                max_context_chars=int(context.get("max_chars") or 0),
                truncated=context.get("truncated") is True,
                input_components=input_components,
                system_sections=system_sections,
                visible_tools=visible_tools,
                dropped_tools=dropped_tools,
                tools_changed=bool(previous_tools and visible_tools != previous_tools),
                active_skills=active_skills,
                skills_changed=bool(previous_skills and active_skills != previous_skills),
                selected_files=selected_files,
                files_seen=tuple(files_seen),
                working_memory_summary=str(context.get("working_memory_summary") or ""),
                model_name=str(model_usage.get("model") or "未记录"),
                model_response_summary=str(llm_call.get("llm_response_summary") or ""),
                reasoning_tokens=int(model_usage.get("reasoning_tokens") or 0),
                estimated_cost_usd=float(model_usage.get("estimated_cost_usd") or 0.0),
                tool_decisions=tool_decisions,
            )
        )

        previous_evidence = _feedback_for_next_turn(tool_decisions)
        previous_messages = message_count
        previous_tokens = estimated_tokens
        previous_tools = visible_tools
        previous_skills = active_skills
        _remember_read_files(files_seen, tool_decisions)
        saw_validation_failure = saw_validation_failure or _has_validation_result(
            tool_decisions, succeeded=False
        )
        saw_write = saw_write or any(
            decision.tool_name in _WRITE_TOOLS for decision in tool_decisions
        )
        saw_validation_pass = saw_validation_pass or _has_validation_result(
            tool_decisions, succeeded=True
        )

    return tuple(turns)


_READ_TOOLS = {"list_files", "read_file", "grep", "grep_search", "git_status"}
_WRITE_TOOLS = {"replace_text", "write_file"}
_VALIDATION_TOOLS = {"python_validation", "run_command"}


def _build_tool_decisions(
    events: list[dict[str, Any]],
) -> tuple[ToolDecision, ...]:
    actions = [event for event in events if event.get("event_type") == "action"]
    observations = [
        event for event in events if event.get("event_type") == "tool_observation"
    ]
    if not observations:
        observations = [
            event for event in events if event.get("event_type") == "observation"
        ]

    decisions: list[ToolDecision] = []
    for index, action in enumerate(actions):
        tool_name = str(action.get("tool_call") or "unknown_tool")
        arguments = _mapping(action.get("tool_arguments"))
        observation = observations[index] if index < len(observations) else None
        succeeded = (
            bool(observation.get("success")) if observation is not None else None
        )
        decisions.append(
            ToolDecision(
                tool_name=tool_name,
                target=_tool_target(tool_name, arguments),
                arguments=arguments,
                succeeded=succeeded,
                feedback=_summarize_tool_feedback(
                    tool_name=tool_name,
                    target=_tool_target(tool_name, arguments),
                    observation=observation,
                ),
            )
        )
    return tuple(decisions)


def _classify_turn(decisions: tuple[ToolDecision, ...]) -> tuple[str, str]:
    if not decisions:
        return "形成答案", "模型没有继续请求工具，本轮进入结果收口。"
    names = {decision.tool_name for decision in decisions}
    if names & _WRITE_TOOLS:
        write_count = sum(
            decision.tool_name in _WRITE_TOOLS for decision in decisions
        )
        return (
            "修改代码",
            f"本轮共 {len(decisions)} 个 ToolCall，其中 {write_count} 个会修改文件。",
        )
    if names & _VALIDATION_TOOLS:
        failed = any(decision.succeeded is False for decision in decisions)
        return (
            ("验证失败", "验证结果将作为下一轮的新证据。")
            if failed
            else ("验证通过", "验证证据支持继续扩大回归范围或形成结论。")
        )
    if names <= _READ_TOOLS | {"git_diff"}:
        return "检索证据", f"模型提出 {len(decisions)} 个只读工具调用。"
    return "执行工具", f"模型提出 {len(decisions)} 个工具调用。"


def _key_turn_reason(
    *,
    step: int,
    tool_decisions: tuple[ToolDecision, ...],
    compacted: bool,
    saw_validation_failure: bool,
    saw_write: bool,
    saw_validation_pass: bool,
) -> str:
    if compacted:
        return "上下文首次触发压缩"
    if _has_validation_result(tool_decisions, succeeded=False):
        return "新增失败证据，后续路径应发生变化"
    if not saw_write and any(
        decision.tool_name in _WRITE_TOOLS for decision in tool_decisions
    ):
        return "从诊断转入代码修改"
    if (
        saw_validation_failure
        and not saw_validation_pass
        and _has_validation_result(tool_decisions, succeeded=True)
    ):
        return "修改后的验证首次转绿"
    if step == 1:
        return "初始任务进入 AgentLoop"
    if not tool_decisions:
        return "模型停止调用工具并形成最终回答"
    return ""


def _has_validation_result(
    decisions: tuple[ToolDecision, ...],
    *,
    succeeded: bool,
) -> bool:
    return any(
        decision.tool_name in _VALIDATION_TOOLS
        and decision.succeeded is succeeded
        for decision in decisions
    )


def _feedback_for_next_turn(
    decisions: tuple[ToolDecision, ...],
) -> tuple[str, ...]:
    if not decisions:
        return ("上一轮没有新增工具 Observation。",)
    return tuple(decision.feedback for decision in decisions)


def _remember_read_files(
    files_seen: list[str],
    decisions: tuple[ToolDecision, ...],
) -> None:
    for decision in decisions:
        if decision.tool_name != "read_file" or not decision.target:
            continue
        if decision.target not in files_seen:
            files_seen.append(decision.target)


def _tool_target(tool_name: str, arguments: dict[str, Any]) -> str:
    for key in ("validation_target", "path", "command", "pattern", "query"):
        value = str(arguments.get(key) or "").strip()
        if value:
            return _short_path(value) if key in {"path", "validation_target"} else value
    return tool_name


def _summarize_tool_feedback(
    *,
    tool_name: str,
    target: str,
    observation: dict[str, Any] | None,
) -> str:
    if observation is None:
        return f"{tool_name} · {target} · 没有记录 Observation"
    succeeded = observation.get("success") is True
    raw = str(
        observation.get("observation")
        or observation.get("observation_summary")
        or observation.get("error")
        or ""
    )
    if tool_name in _VALIDATION_TOOLS:
        exit_code = _first_match(r"exit_code=(\d+)", raw)
        passed = _first_match(r"(\d+) passed", raw)
        failed = _first_match(r"(\d+) failed", raw)
        parts = ["通过" if succeeded else "未通过"]
        if exit_code:
            parts.append(f"exit={exit_code}")
        if passed:
            parts.append(f"{passed} passed")
        if failed:
            parts.append(f"{failed} failed")
        return f"{tool_name} · {target} · " + " · ".join(parts)
    result = "成功" if succeeded else "失败"
    return f"{tool_name} · {target} · {result}"


def _event_payload(
    events: list[dict[str, Any]],
    event_type: str,
    payload_key: str,
) -> dict[str, Any]:
    return _mapping(_last_event(events, event_type).get(payload_key))


def _last_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    return next(
        (
            event
            for event in reversed(events)
            if event.get("event_type") == event_type
        ),
        {},
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _short_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if "/workspace/" in normalized:
        return normalized.split("/workspace/", 1)[1]
    path = Path(normalized)
    if len(path.parts) > 3:
        return "/".join(path.parts[-3:])
    return normalized


def _first_match(pattern: str, value: str) -> str:
    match = re.search(pattern, value)
    return match.group(1) if match else ""
