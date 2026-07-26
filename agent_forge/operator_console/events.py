"""把 RuntimeEvent 投影成适合人读的操作台时间线。

Runtime 会保留完整 Evidence；这里默认只展示 Agent 主链。底层 hook、哈希和
仓储路径仍可通过“显示底层事件”查看，但不会淹没模型、工具和状态变化。
"""

from __future__ import annotations

import json
from pathlib import PurePath
from queue import Empty, SimpleQueue

from agent_forge.observability.domain.live_event import RuntimeEvent


_EVENT_LABELS = {
    "run.started": "运行开始",
    "turn.started": "新一轮",
    "context.completed": "上下文装配",
    "context.window": "上下文窗口",
    "model.started": "模型请求",
    "model.completed": "模型响应",
    "tool.proposed": "工具提议",
    "tool.started": "工具开始",
    "tool.recorded": "工具记录",
    "tool.completed": "工具结果",
    "skill.selected": "Skill 选择",
    "human.required": "等待人工输入",
    "approval.updated": "审批状态",
    "run.control": "运行控制",
    "checkpoint.saved": "Checkpoint",
    "run.completed": "运行结束",
    "run.published": "证据发布",
}
_RUN_LEVEL_EVENTS = {
    "run.started",
    "run.completed",
    "run.published",
}
_CORE_EVENT_NAMES = {
    "run.started",
    "turn.started",
    "context.window",
    "model.started",
    "model.completed",
    "tool.proposed",
    "tool.completed",
    "human.required",
    "approval.updated",
    "run.control",
    "checkpoint.saved",
    "run.completed",
    "run.published",
}


class RuntimeEventBuffer:
    """线程安全事件缓冲区；Runtime 写入，Textual 主线程批量读取。"""

    def __init__(self) -> None:
        self._events: SimpleQueue[RuntimeEvent] = SimpleQueue()

    def on_event(self, event: RuntimeEvent) -> None:
        """RuntimeEventListener 入口：只入队，不阻塞 AgentLoop。"""

        self._events.put(event)

    def drain(self, *, limit: int = 200) -> list[RuntimeEvent]:
        """按产生顺序取出至多 ``limit`` 条事件。"""

        drained: list[RuntimeEvent] = []
        while len(drained) < limit:
            try:
                drained.append(self._events.get_nowait())
            except Empty:
                break
        return drained


def should_render_event(
    event: RuntimeEvent,
    *,
    include_infrastructure: bool = False,
) -> bool:
    """判断事件是否属于默认主线；运行中的机械 checkpoint 默认折叠。"""

    if include_infrastructure:
        return True
    if event.name not in _CORE_EVENT_NAMES:
        return False
    if event.name != "checkpoint.saved":
        return True
    status = str(event.payload.get("status") or "").lower()
    stop_reason = str(event.payload.get("stop_reason") or "")
    return status not in {"", "running"} or bool(stop_reason)


def render_event(
    event: RuntimeEvent,
    *,
    include_infrastructure: bool = False,
) -> str:
    """把一个事件压缩成单行，不展示隐藏思维链或大段内部 JSON。"""

    label = _EVENT_LABELS.get(event.name, event.name)
    outcome = "" if event.success else " [失败]"
    scope = "Run" if event.name in _RUN_LEVEL_EVENTS else f"Step {event.step}"
    details = _render_summary(event)
    if include_infrastructure and event.name not in _CORE_EVENT_NAMES:
        details = _render_payload(event.payload, max_chars=220)
    prefix = f"{scope:<7} {label}{outcome}"
    return f"{prefix}  ·  {details}" if details else prefix


def _render_summary(event: RuntimeEvent) -> str:
    payload = event.payload
    if event.name == "run.started":
        task_chars = payload.get("task_chars")
        return f"任务 {task_chars} chars" if task_chars is not None else ""
    if event.name == "turn.started":
        return "开始准备上下文"
    if event.name == "context.window":
        window = _mapping(payload.get("context_window"))
        tokens = window.get("estimated_tokens_after")
        compacted = bool(window.get("compacted"))
        if tokens is None:
            return "上下文已装配"
        suffix = "，已压缩" if compacted else "，无需压缩"
        return f"约 {tokens} tokens{suffix}"
    if event.name == "model.started":
        request = _mapping(payload.get("model_request"))
        parts = [
            _count_text(request.get("messages_count"), "messages"),
            _count_text(request.get("tool_count"), "tools"),
            _approx_count_text(
                request.get("estimated_prompt_tokens"),
                "input tokens",
            ),
        ]
        return " · ".join(part for part in parts if part) or "请求已发送"
    if event.name == "model.completed":
        usage = _mapping(payload.get("model_usage"))
        model = str(usage.get("model") or "")
        total_tokens = _count_text(usage.get("total_tokens"), "tokens")
        latency_ms = usage.get("latency_ms")
        latency = (
            f"{float(latency_ms) / 1000:.2f}s"
            if isinstance(latency_ms, (int, float))
            else ""
        )
        cost = usage.get("estimated_cost_usd")
        cost_text = (
            f"${float(cost):.4f}" if isinstance(cost, (int, float)) else ""
        )
        return " · ".join(
            part for part in (model, total_tokens, latency, cost_text) if part
        )
    if event.name in {"tool.proposed", "tool.completed"}:
        tool_name = str(
            payload.get("tool_call")
            or payload.get("tool_name")
            or "unknown tool"
        )
        arguments = _mapping(payload.get("arguments"))
        target = (
            arguments.get("path")
            or arguments.get("target")
            or arguments.get("query")
        )
        target_text = _compact_target(target)
        return " · ".join(part for part in (tool_name, target_text) if part)
    if event.name == "human.required":
        return "等待操作员回答"
    if event.name == "approval.updated":
        approval = _mapping(payload.get("approval_request"))
        fingerprint = _mapping(approval.get("operation_fingerprint"))
        tool_name = str(approval.get("tool_name") or "write operation")
        status = str(approval.get("status") or "pending").upper()
        target = str(
            fingerprint.get("path")
            or approval.get("action")
            or ""
        )
        return " · ".join(part for part in (tool_name, target, status) if part)
    if event.name == "run.control":
        action = payload.get("action") or payload.get("signal")
        return str(action or "控制信号已处理")
    if event.name == "checkpoint.saved":
        status = str(payload.get("status") or "saved").upper()
        messages = payload.get("messages_count")
        observations = payload.get("observations_count")
        counts = ""
        if messages is not None or observations is not None:
            counts = f"{messages or 0} messages · {observations or 0} observations"
        return " · ".join(part for part in (status, counts) if part)
    if event.name == "run.completed":
        status = str(payload.get("run_status") or "completed").upper()
        stop_reason = str(payload.get("stop_reason") or "")
        return " · ".join(part for part in (status, stop_reason) if part)
    if event.name == "run.published":
        return "Evidence 已落盘"
    return ""


def _render_payload(payload: object, *, max_chars: int) -> str:
    if not isinstance(payload, dict):
        return _short_text(payload, max_chars=max_chars)
    visible = {
        str(key): value
        for key, value in payload.items()
        if key
        not in {
            "prompt",
            "request",
            "reasoning_content",
            "raw_response",
        }
    }
    if not visible:
        return ""
    return _short_text(
        json.dumps(visible, ensure_ascii=False, sort_keys=True, default=str),
        max_chars=max_chars,
    )


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): child for key, child in value.items()}


def _count_text(value: object, unit: str) -> str:
    return f"{value} {unit}" if isinstance(value, int) else ""


def _approx_count_text(value: object, unit: str) -> str:
    return f"~{value} {unit}" if isinstance(value, int) else ""


def _compact_target(value: object) -> str:
    """保留工具目标而折叠本机绝对路径，避免展示环境噪音。"""

    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if text.startswith("/"):
        parts = PurePath(text).parts
        text = "/".join(parts[-2:]) if len(parts) > 2 else PurePath(text).name
        text = f"…/{text}"
    return _short_text(text, max_chars=80)


def _short_text(value: object, *, max_chars: int) -> str:
    text = str(value).replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1]}…"
