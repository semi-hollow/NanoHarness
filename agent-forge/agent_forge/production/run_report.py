import json
from pathlib import Path

from agent_forge.production.diff_tracker import DiffSummary


class RunReportWriter:
    """Write human-readable run reports beside machine-readable artifacts."""

    def __init__(self, output_dir: str | Path):
        """Store the run artifact directory."""

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        task: str,
        mode: str,
        trace_path: str,
        diff: DiffSummary,
        final_answer: str,
        metrics: dict,
    ) -> None:
        """Persist report.md, metrics.json, and diff.patch for one run."""

        (self.output_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.output_dir / "diff.patch").write_text(diff.patch, encoding="utf-8")
        lines = [
            "# Agent Forge Run Report",
            "",
            f"- mode: `{mode}`",
            f"- task: {task}",
            f"- trace: `{trace_path}`",
            f"- changed_files: {len(diff.changed_files)}",
            "",
            "## Changed Files",
            "",
        ]
        if diff.changed_files:
            lines.extend(f"- `{path}`" for path in diff.changed_files)
        else:
            lines.append("none")
        lines.extend(["", "## Final Answer", "", final_answer or "none", "", "## Metrics", ""])
        for key, value in metrics.items():
            lines.append(f"- {key}: {value}")
        (self.output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
