"""Single-Run artifact manifest 的文件适配器。

规范上游是 ``Harness.run``；下游是 Run Story、inspection 与 Workbench。它只登记已经
存在的文件，不从文件名推断任务是否解决。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_forge.observability.domain.run_story import RunArtifact, RunManifest


@dataclass(frozen=True)
class _ArtifactSpec:
    kind: str
    producer_symbol: str
    flow_stage: str
    semantic_consumers: tuple[str, ...]
    evidence_level: str
    proves: tuple[str, ...]
    does_not_prove: tuple[str, ...]
    derived_from: tuple[str, ...] = ()
    source_event_refs: tuple[str, ...] = ()


_KNOWN_ARTIFACTS: dict[str, _ArtifactSpec] = {
    "run_request.json": _ArtifactSpec(
        "run_request",
        "Harness.run",
        "request",
        ("RunStory", "inspection"),
        "fact",
        ("记录了本次 run 的任务输入和 Runtime 策略",),
        ("模型已被调用", "任务已完成"),
    ),
    "resolved_config.json": _ArtifactSpec(
        "resolved_config",
        "cli.repository.run_repository_task -> Harness.run",
        "request",
        ("RunStory", "replay"),
        "fact",
        ("记录了 CLI、环境变量、config 和默认值合并后的非密钥配置",),
        ("外部凭据有效", "模型调用成功"),
    ),
    "execution_environment.json": _ArtifactSpec(
        "execution_environment",
        "ExecutionEnvironment.write_manifest",
        "request",
        ("RunStory", "replay", "benchmark diagnostics"),
        "fact",
        ("记录了实际 workspace、Git 基线和隔离模式",),
        ("隔离能抵御 hostile multi-tenant workload",),
    ),
    "trace.json": _ArtifactSpec(
        "trace",
        "TraceRecorder.publish",
        "evidence",
        ("RunStory", "usage projection", "replay", "evaluation"),
        "fact",
        ("记录了实际 Runtime 事件和状态转换",),
        ("candidate patch 正确", "official resolved"),
        source_event_refs=("all",),
    ),
    "usage.json": _ArtifactSpec(
        "usage_projection",
        "write_usage_artifacts",
        "evidence",
        ("RunStory", "Workbench", "cost comparison"),
        "derived",
        ("汇总了 trace 中可计算的 token、tool 和 checkpoint 指标",),
        ("provider 账单完全一致", "任务已解决"),
        derived_from=("trace",),
    ),
    "usage_report.md": _ArtifactSpec(
        "usage_report",
        "render_usage_markdown",
        "evidence",
        ("human reader", "inspection"),
        "presentation",
        ("以可读形式展示 usage projection",),
        ("Markdown 是独立事实源", "official resolved"),
        derived_from=("usage_projection",),
    ),
    "final_answer.txt": _ArtifactSpec(
        "final_answer",
        "Harness.run",
        "artifacts",
        ("caller", "RunStory", "benchmark case runner"),
        "candidate",
        ("模型产生了最终文本",),
        ("文本中的完成声明真实", "测试通过"),
    ),
    "patch.diff": _ArtifactSpec(
        "patch",
        "ExecutionEnvironment.diff -> Harness.run",
        "artifacts",
        ("local evaluator", "official evaluator", "RunStory"),
        "candidate",
        ("非空时证明生成了 candidate patch",),
        ("测试通过", "问题解决", "official resolved"),
    ),
    "resume_link.json": _ArtifactSpec(
        "resume_link",
        "cli.resume.write_resume_link",
        "lifecycle",
        ("RunStory", "inspection"),
        "fact",
        ("记录了 continuation 与前序 run/checkpoint 的关系",),
        ("恢复了隐藏模型状态",),
    ),
    "resume_chain.md": _ArtifactSpec(
        "resume_chain_report",
        "cli.resume.write_resume_link",
        "lifecycle",
        ("human reader",),
        "presentation",
        ("以可读形式展示 continuation 链",),
        ("它是 checkpoint 事实源",),
        derived_from=("resume_link",),
    ),
}

_CHECKPOINT_SPEC = _ArtifactSpec(
    "checkpoint",
    "RunLifecycle.update / stop",
    "lifecycle",
    ("Harness.resume", "operator control", "RunStory"),
    "fact",
    ("记录了可恢复任务状态、消息和 stop reason",),
    ("恢复了进程内对象或模型 KV cache",),
    source_event_refs=("task_state_checkpoint",),
)


# 主要入口：发布完整 single-run artifact 目录，替代外围按文件名猜测 owner。
def write_run_manifest(
    run_dir: str | Path,
    *,
    run_id: str,
    task: str,
    status: str,
    stop_reason: str,
) -> Path:
    """登记当前 run 的全部文件；未知文件显式标记而不是静默忽略。"""

    root = Path(run_dir)
    known_paths: set[str] = set()
    artifacts: list[RunArtifact] = []
    for relative_path, spec in _KNOWN_ARTIFACTS.items():
        path = root / relative_path
        if path.is_file():
            known_paths.add(relative_path)
            artifacts.append(_record(path, root, spec))

    task_state = root / "task_state"
    if task_state.is_dir():
        for path in sorted(task_state.glob("*.json")):
            relative = path.relative_to(root).as_posix()
            known_paths.add(relative)
            artifacts.append(_record(path, root, _CHECKPOINT_SPEC))

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in known_paths or relative == "run_manifest.json":
            continue
        artifacts.append(
            _record(
                path,
                root,
                _ArtifactSpec(
                    "unclassified",
                    "unknown",
                    "unknown",
                    ("inspection",),
                    "unknown",
                    (),
                    ("尚未登记 owner；不能据此形成项目结论",),
                ),
            )
        )

    manifest = RunManifest(
        run_id=run_id,
        task=task,
        status=status,
        stop_reason=stop_reason,
        artifacts=tuple(sorted(artifacts, key=lambda item: item.relative_path)),
    )
    path = root / "run_manifest.json"
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def read_run_manifest(path: str | Path) -> RunManifest:
    """读取并验证 manifest 根结构；claim 语义仍由类型化模型承载。"""

    payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("run manifest root must be an object")
    return RunManifest.from_dict(payload)


def refresh_run_manifest(run_dir: str | Path) -> Path:
    """保留 run 结论并重新登记后来生成的 lifecycle artifact。"""

    root = Path(run_dir)
    manifest = read_run_manifest(root / "run_manifest.json")
    return write_run_manifest(
        root,
        run_id=manifest.run_id,
        task=manifest.task,
        status=manifest.status,
        stop_reason=manifest.stop_reason,
    )


def _record(path: Path, root: Path, spec: _ArtifactSpec) -> RunArtifact:
    content = path.read_bytes()
    relative = path.relative_to(root).as_posix()
    return RunArtifact(
        artifact_id=_artifact_id(relative),
        kind=spec.kind,
        relative_path=relative,
        producer_symbol=spec.producer_symbol,
        flow_stage=spec.flow_stage,
        semantic_consumers=spec.semantic_consumers,
        evidence_level=spec.evidence_level,
        proves=spec.proves,
        does_not_prove=spec.does_not_prove,
        derived_from=spec.derived_from,
        source_event_refs=spec.source_event_refs,
        rebuildable=bool(spec.derived_from),
        deletion_impact=_deletion_impact(spec),
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
    )


def _artifact_id(relative_path: str) -> str:
    return relative_path.replace("/", ":").removesuffix(".json").removesuffix(".md")


def _deletion_impact(spec: _ArtifactSpec) -> str:
    if spec.derived_from:
        return "删除会丢失当前投影；源 artifact 仍在时可以重建。"
    if spec.evidence_level == "candidate":
        return "删除会丢失本次不可确定复现的 candidate 输出。"
    if spec.evidence_level == "unknown":
        return "影响未知；先登记 owner、consumer 与来源，再决定是否删除。"
    return "删除会丢失本次运行事实、恢复依据或审计链的一部分。"


__all__ = ["read_run_manifest", "refresh_run_manifest", "write_run_manifest"]
