from __future__ import annotations

from pathlib import Path

from agent_forge.observability.adapters.json_trace import JsonTraceRecorder, TraceRecorder
from agent_forge.observability.adapters.usage_files import (
    read_trace,
    usage_artifact_paths,
    write_usage_files,
)
from agent_forge.observability.application.usage import BuildUsageReport
from agent_forge.observability.domain.event import TraceEvent, TraceEventType, TraceRecord
from agent_forge.observability.domain.evidence import EvidenceItem, EvidenceLedger
from agent_forge.observability.domain.metrics import summarize, summarize_trace
from agent_forge.observability.domain.usage import build_usage_report
from agent_forge.observability.presentation.usage_report import render_usage_markdown
from agent_forge.observability.presentation.replay import render_trace_replay


# PRIMARY ENTRYPOINT: derive and persist usage evidence from one trace.
def write_usage_artifacts(
    trace_path: str | Path,
    output_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """Build usage artifacts through explicit read, projection, and render stages."""

    trace_file = Path(trace_path)
    usage = BuildUsageReport().execute(read_trace(trace_file))
    usage_json, usage_markdown = usage_artifact_paths(trace_file, output_dir)
    return write_usage_files(
        usage,
        json_path=usage_json,
        markdown_path=usage_markdown,
        markdown=render_usage_markdown(usage),
    )


def summarize_trace_file(path: str | Path) -> dict:
    return summarize_trace(read_trace(Path(path)))


def replay_trace_file(path: str | Path) -> str:
    """读取 trace artifact 并渲染终端时间线。"""

    return render_trace_replay(read_trace(Path(path)))


__all__ = [
    "EvidenceItem",
    "EvidenceLedger",
    "JsonTraceRecorder",
    "TraceEvent",
    "TraceEventType",
    "TraceRecord",
    "TraceRecorder",
    "build_usage_report",
    "render_usage_markdown",
    "replay_trace_file",
    "summarize",
    "summarize_trace",
    "summarize_trace_file",
    "usage_artifact_paths",
    "write_usage_artifacts",
]
