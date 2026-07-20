"""Run Story 的终端/Markdown renderer；不读取文件，也不推断新结论。"""

from __future__ import annotations

from agent_forge.observability.domain.run_story import RunStory


def render_run_story(story: RunStory) -> str:
    """把统一 Read Model 渲染为可折叠阅读的单页文本。"""

    lines = [
        "# Run Story",
        "",
        f"- run_id: `{story.run_id}`",
        f"- status: `{story.status}`",
        f"- stop_reason: `{story.stop_reason or '-'}`",
        f"- task: {story.task or '(unknown)'}",
        "",
        "## 黄金主链",
        "",
    ]
    for index, stage in enumerate(story.stages, start=1):
        observed = "observed" if stage.observed else "not observed"
        lines.extend(
            [
                f"### {index}. {stage.title} — {observed}",
                "",
                f"- owner: `{stage.owner_symbol}`",
                f"- upstream: `{stage.canonical_upstream}`",
                f"- invariant: {stage.invariant}",
                f"- events: `{stage.event_count}`",
                f"- artifacts: `{', '.join(stage.artifact_ids) or '-'}`",
                "",
            ]
        )

    lines.extend(["## Evidence Ladder", ""])
    for level in ("candidate", "local", "official"):
        lines.append(f"- {level}: `{story.evidence_ladder.get(level, 'unknown')}`")

    lines.extend(["", "## Artifact Lineage", ""])
    for artifact in story.artifacts:
        lines.extend(
            [
                f"### `{artifact.relative_path}`",
                "",
                f"- kind / level: `{artifact.kind}` / `{artifact.evidence_level}`",
                f"- producer: `{artifact.producer_symbol}`",
                f"- consumers: `{', '.join(artifact.semantic_consumers) or '-'}`",
                f"- rebuildable: `{str(artifact.rebuildable).lower()}`",
                f"- deletion impact: {artifact.deletion_impact or '未登记'}",
                f"- proves: {'；'.join(artifact.proves) or '没有登记正向 claim'}",
                (
                    "- does not prove: "
                    f"{'；'.join(artifact.does_not_prove) or '没有登记边界'}"
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_run_story"]
