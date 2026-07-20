from __future__ import annotations

from pathlib import Path

from agent_forge.observability.adapters.json_trace import JsonTraceRecorder, TraceRecorder
from agent_forge.observability.adapters.run_manifest_files import (
    read_run_manifest,
    refresh_run_manifest,
    write_run_manifest,
)
from agent_forge.observability.adapters.usage_files import (
    read_trace,
    usage_artifact_paths,
    write_usage_files,
)
from agent_forge.observability.application.usage import BuildUsageReport
from agent_forge.observability.domain.event import TraceEvent, TraceEventType, TraceRecord
from agent_forge.observability.domain.evidence import EvidenceItem, EvidenceLedger
from agent_forge.observability.domain.metrics import summarize, summarize_trace
from agent_forge.observability.domain.run_story import (
    RunArtifact,
    RunManifest,
    RunStory,
    project_run_story,
)
from agent_forge.observability.domain.usage import build_usage_report
from agent_forge.observability.presentation.usage_report import render_usage_markdown
from agent_forge.observability.presentation.replay import render_trace_replay
from agent_forge.observability.presentation.run_story import render_run_story

# 主要入口：从 trace 事实投影并发布 usage.json 与 usage_report.md。
def write_usage_artifacts(
    trace_path: str | Path,
    output_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """从 trace 构造并写入 usage JSON 与 Markdown 证据。"""

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


# 主要入口：从 run manifest 与 trace 构造唯一 Single-Run 解释模型。
def load_run_story(run_dir: str | Path) -> RunStory:
    """读取事实 artifact；缺失 trace 时仍保留 manifest 血缘，不伪造事件。"""

    root = Path(run_dir)
    manifest = read_run_manifest(root / "run_manifest.json")
    trace_path = root / "trace.json"
    trace = read_trace(trace_path) if trace_path.exists() else None
    return project_run_story(manifest, trace)

__all__ = [
    "EvidenceItem",
    "EvidenceLedger",
    "JsonTraceRecorder",
    "RunArtifact",
    "RunManifest",
    "RunStory",
    "TraceEvent",
    "TraceEventType",
    "TraceRecord",
    "TraceRecorder",
    "build_usage_report",
    "load_run_story",
    "project_run_story",
    "read_run_manifest",
    "refresh_run_manifest",
    "render_run_story",
    "render_usage_markdown",
    "replay_trace_file",
    "summarize",
    "summarize_trace",
    "summarize_trace_file",
    "usage_artifact_paths",
    "write_usage_artifacts",
    "write_run_manifest",
]
