from __future__ import annotations

import json
import re
from pathlib import Path

from .report import render_multi_agent_report
from .types import Artifact, MultiAgentRunSummary, RoleSpec


class ArtifactStore:
    """Filesystem handoff layer for coordinator-driven agents.

    Agents do not share hidden chat state. Every role writes a concrete artifact
    that later roles can inspect through a prompt handoff. This makes review and
    verification traceable instead of relying on free-form agent chatter.
    """

    def __init__(self, run_dir: Path) -> None:
        """Create the multi-agent artifact directory under one run dir."""

        self.run_dir = Path(run_dir)
        self.root = self.run_dir / "multi_agent"
        self.artifacts_dir = self.root / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts: list[Artifact] = []

    def write_role_artifact(self, role: RoleSpec, content: str, round_index: int) -> Artifact:
        """Persist one role output and update the artifact index."""

        slug = _slug(f"r{round_index:02d}-{role.name}-{role.output_artifact}")
        path = self.artifacts_dir / f"{slug}.md"
        body = "\n".join(
            [
                f"# {role.name} - {role.output_artifact}",
                "",
                f"- round: `{round_index}`",
                f"- role: `{role.role}`",
                "",
                "## Output",
                "",
                content.strip() or "(empty output)",
                "",
            ]
        )
        path.write_text(body, encoding="utf-8")
        artifact = Artifact(
            id=slug,
            role=role.name,
            kind=role.output_artifact,
            path=path,
            summary=_summarize(content),
            round_index=round_index,
        )
        self.artifacts.append(artifact)
        self.write_index()
        return artifact

    def write_text_artifact(self, role_name: str, kind: str, content: str, round_index: int = 0) -> Artifact:
        """Persist coordinator-produced artifacts such as final summaries."""

        slug = _slug(f"r{round_index:02d}-{role_name}-{kind}")
        path = self.artifacts_dir / f"{slug}.md"
        path.write_text(content.strip() + "\n", encoding="utf-8")
        artifact = Artifact(slug, role_name, kind, path, _summarize(content), round_index)
        self.artifacts.append(artifact)
        self.write_index()
        return artifact

    def write_index(self) -> Path:
        """Write artifact_index.json for later UI/report readers."""

        index_path = self.root / "artifact_index.json"
        index_path.write_text(
            json.dumps([artifact.to_dict() for artifact in self.artifacts], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return index_path

    def write_summary(self, summary: MultiAgentRunSummary) -> tuple[Path, Path]:
        """Write machine and human coordinator reports."""

        summary.artifacts = list(self.artifacts)
        summary_path = self.root / "multi_agent_summary.json"
        report_path = self.root / "multi_agent_report.md"
        summary.summary_path = summary_path
        summary.report_path = report_path
        summary_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(render_multi_agent_report(summary), encoding="utf-8")
        return summary_path, report_path

    def render_handoff_context(self, limit_chars: int = 12000) -> str:
        """Render prior artifacts for the next role prompt.

        Newest artifacts are rendered first because review/verifier roles must
        evaluate the current round before older history. The older artifacts are
        still useful as audit trail, but they should not consume the prompt
        budget before the latest candidate draft or patch.
        """

        sections: list[str] = []
        budget = limit_chars
        for artifact in reversed(self.artifacts[-8:]):
            text = artifact.path.read_text(encoding="utf-8")
            excerpt = text[: min(len(text), max(0, budget))]
            sections.append(
                "\n".join(
                    [
                        f"### Artifact {artifact.id}",
                        f"- role: {artifact.role}",
                        f"- kind: {artifact.kind}",
                        f"- path: {artifact.path}",
                        "",
                        excerpt,
                    ]
                )
            )
            budget -= len(excerpt)
            if budget <= 0:
                sections.append("\n[artifact context truncated]\n")
                break
        return "\n\n".join(sections) if sections else "(no prior artifacts)"


def _slug(value: str) -> str:
    """Return a filesystem-safe artifact id."""

    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-").lower()


def _summarize(content: str, limit: int = 240) -> str:
    """Return a compact single-line artifact summary."""

    return " ".join((content or "").strip().split())[:limit]
