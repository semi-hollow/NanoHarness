"""把 Run 事件先写入 JSONL journal，再投影为最终 ``trace.json``。

系统角色：JSONL 是按事件追加、每行 flush 的进程崩溃恢复来源；它没有逐事件 fsync，
因此不能宣称每条记录都具备 OS/power-loss durability。``trace.json`` 是终态派生投影，
不是另一套独立事实源。

折叠导航：1 Writer 生命周期；2 事件追加；3 终态投影；4 Journal 严格读取。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, TextIO

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.observability.domain.event import TraceEvent, TraceEventType, TraceRecord
from agent_forge.observability.domain.metrics import summarize
from agent_forge.observability.presentation.trace_summary import render_trace_summary
from agent_forge.runtime.ports.events import EventSink

if TYPE_CHECKING:
    from agent_forge.runtime.domain.task import TaskCheckpoint


TRACE_SCHEMA_VERSION = 2


class JsonTraceRecorder(EventSink):
    """逐行追加并 flush ``trace.jsonl``，终止时生成可读投影。"""

    # region 1. Writer 生命周期：一个 Run 一个 journal handle
    def __init__(
        self,
        path: str,
        verbose: bool = False,
        write_summary_file: bool = False,
    ) -> None:
        self.path = path
        self.journal_path = str(Path(path).with_suffix(".jsonl"))
        self.verbose = verbose
        self.write_summary_file = write_summary_file
        self.run_id = str(uuid.uuid4())
        self.events: list[TraceRecord] = []
        self.started_at = time.time()
        self._last_event_at = self.started_at
        self.task = ""
        self.stop_reason = ""
        self.stop_output = ""
        self.final_answer: str | None = None
        journal = Path(self.journal_path)
        journal.parent.mkdir(parents=True, exist_ok=True)
        self._journal: TextIO | None = journal.open("w", encoding="utf-8")
        self._append_record(
            {
                "record_type": "trace_header",
                "schema_version": TRACE_SCHEMA_VERSION,
                "run_id": self.run_id,
                "start_time": self.started_at,
            }
        )

    def set_run_context(
        self,
        task: str = "",
        stop_reason: str = "",
        stop_output: str = "",
        final_answer: str | None = None,
    ) -> None:
        """追加顶层上下文变化；accepted final answer 可为 ``null``。"""

        update: dict[str, Any] = {"record_type": "run_context"}
        if task:
            self.task = task
            update["task"] = task
        if stop_reason:
            self.stop_reason = stop_reason
            update["stop_reason"] = stop_reason
        if stop_output:
            self.stop_output = stop_output
            update["stop_output"] = stop_output
        if final_answer is not None:
            self.final_answer = final_answer
            update["final_answer"] = final_answer
        if len(update) > 1:
            self._append_record(update)
    # endregion 1. Writer 生命周期结束

    # region 2. 事件追加：构造 TraceEvent → 内存镜像 → JSONL 一行 → flush
    def add(
        self,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        success: bool = True,
        error: str = "",
        **data: Any,
    ) -> None:
        """把一个完整事件同步追加为一行，并立即 flush。"""

        self._append(
            step,
            agent_name,
            event_type,
            success=success,
            error=error,
            data=data,
        )

    def record_task_state_checkpoint(
        self,
        *,
        step: int,
        agent_name: str,
        checkpoint: "TaskCheckpoint",
    ) -> None:
        self._append(
            step,
            agent_name,
            "task_state_checkpoint",
            data={"task_state": checkpoint.to_dict()},
        )

    def record_event(
        self,
        *,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        success: bool = True,
        error: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        self._append(
            step,
            agent_name,
            event_type,
            success=success,
            error=error,
            data=dict(data or {}),
        )

    def _append(
        self,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        *,
        success: bool = True,
        error: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        now = time.time()
        event = TraceEvent(
            run_id=self.run_id,
            step=step,
            agent_name=agent_name,
            event_type=event_type,
            duration_ms=int((now - self._last_event_at) * 1000),
            success=success,
            error=error,
            data=data or {},
        ).to_dict()
        self._last_event_at = now
        self.events.append(event)
        self._append_record({"record_type": "event", **event})
        if self.verbose:
            print(
                f"[trace] step={step} agent={agent_name} "
                f"event={event_type} success={success}"
            )

    def _append_record(self, record: Mapping[str, Any]) -> None:
        """追加完整 JSON 行并 flush Python buffer；这里没有逐事件 ``fsync``。"""

        if self._journal is None:
            raise RuntimeError("trace journal is already closed")
        self._journal.write(json.dumps(record, ensure_ascii=False, default=str))
        self._journal.write("\n")
        self._journal.flush()
    # endregion 2. 事件追加结束

    # region 3. 终态投影：重读 journal → metrics → atomic trace.json → close
    def write(self) -> None:
        """从已 flush journal 重建事实并生成最终 ``trace.json`` projection。"""

        if self._journal is not None:
            self._journal.flush()
        context, events, truncated_tail = read_trace_jsonl(self.journal_path)
        trace = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": context["run_id"],
            "task": context.get("task", ""),
            "start_time": context["start_time"],
            "end_time": time.time(),
            "stop_reason": context.get("stop_reason", ""),
            "stop_output": context.get("stop_output", ""),
            "final_answer": context.get("final_answer"),
            "journal_tail_truncated": truncated_tail,
            "events": events,
            "metrics": summarize(events),
        }
        atomic_write_json(self.path, trace)
        if self.write_summary_file:
            summary_path = Path(self.path).with_name("summary.md")
            summary_path.write_text(render_trace_summary(trace), encoding="utf-8")

    def publish(self) -> None:
        self.write()
        self.close()

    def close(self) -> None:
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    def __del__(self) -> None:  # pragma: no cover - interpreter cleanup fallback
        self.close()
    # endregion 3. 终态投影结束


# region 4. Journal 严格读取：只容忍最后一条未换行的 crash tail
def read_trace_jsonl(
    path: str | Path,
) -> tuple[dict[str, Any], list[TraceRecord], bool]:
    """严格读取 journal；只容忍未换行的最后一条 crash-truncated record。"""

    raw_lines = Path(path).read_bytes().splitlines(keepends=True)
    if not raw_lines:
        raise ValueError("trace journal is empty")

    records: list[dict[str, Any]] = []
    truncated_tail = False
    for index, raw_line in enumerate(raw_lines):
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            is_unterminated_tail = index == len(raw_lines) - 1 and not raw_line.endswith(
                b"\n"
            )
            if is_unterminated_tail:
                truncated_tail = True
                break
            raise ValueError(f"corrupt trace journal line {index + 1}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"trace journal line {index + 1} must be an object")
        records.append(record)

    header = records[0] if records else {}
    if header.get("record_type") != "trace_header":
        raise ValueError("trace journal is missing header")
    schema_version = int(header.get("schema_version") or 0)
    if schema_version != TRACE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported trace schema_version: {schema_version}; "
            f"expected {TRACE_SCHEMA_VERSION}"
        )

    context: dict[str, Any] = {
        "run_id": header.get("run_id", ""),
        "start_time": header.get("start_time", 0.0),
    }
    events: list[TraceRecord] = []
    for index, record in enumerate(records[1:], start=2):
        record_type = record.get("record_type")
        if record_type == "run_context":
            context.update(
                {
                    key: value
                    for key, value in record.items()
                    if key != "record_type"
                }
            )
            continue
        if record_type != "event":
            raise ValueError(f"unknown trace journal record at line {index}")
        event = dict(record)
        event.pop("record_type", None)
        events.append(event)
    return context, events, truncated_tail
# endregion 4. Journal 严格读取结束


TraceRecorder = JsonTraceRecorder

__all__ = [
    "JsonTraceRecorder",
    "read_trace_jsonl",
    "TRACE_SCHEMA_VERSION",
    "TraceRecorder",
]
