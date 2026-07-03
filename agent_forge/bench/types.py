from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BenchCase:
    """One external coding task normalized from SWE-bench.

    Why every field matters:
        ``instance_id`` is the stable benchmark id used by official evaluation.
        ``repo`` tells the runner which GitHub repository to clone.
        ``base_commit`` pins the exact pre-fix state; without it, results are
        irreproducible.
        ``problem_statement`` is the issue text given to the agent.
        ``test_patch`` and ``hints_text`` are optional SWE-bench metadata kept
        for reports, but the agent should not need gold patches to act.
    """

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    test_patch: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "BenchCase":
        """Build from HuggingFace/JSONL row while tolerating schema variants."""

        instance_id = str(data.get("instance_id") or data.get("id") or "")
        repo = str(data.get("repo") or data.get("repository") or "")
        base_commit = str(data.get("base_commit") or data.get("commit") or "")
        problem = str(data.get("problem_statement") or data.get("issue") or data.get("task") or "")
        if not instance_id or not repo or not base_commit or not problem:
            missing = [
                name
                for name, value in {
                    "instance_id": instance_id,
                    "repo": repo,
                    "base_commit": base_commit,
                    "problem_statement": problem,
                }.items()
                if not value
            ]
            raise ValueError(f"SWE-bench case is missing required fields: {', '.join(missing)}")
        return cls(
            instance_id=instance_id,
            repo=repo,
            base_commit=base_commit,
            problem_statement=problem,
            hints_text=str(data.get("hints_text") or ""),
            test_patch=str(data.get("test_patch") or ""),
            raw=dict(data),
        )


@dataclass
class BenchCaseResult:
    """Outcome for one generated prediction before or after official eval."""

    instance_id: str
    repo: str
    workspace: Path
    trace_path: Path
    usage_report_path: Path | None
    patch_path: Path
    status: str
    final_answer: str
    patch_chars: int = 0
    error: str = ""
    evaluation_status: str = "not_evaluated"
    failure_class: str = ""
    diagnosis: str = ""
    diagnosis_evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize result data for ``results.json`` and reports."""

        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "workspace": str(self.workspace),
            "trace_path": str(self.trace_path),
            "usage_report_path": str(self.usage_report_path) if self.usage_report_path else "",
            "patch_path": str(self.patch_path),
            "status": self.status,
            "final_answer": self.final_answer,
            "patch_chars": self.patch_chars,
            "error": self.error,
            "evaluation_status": self.evaluation_status,
            "failure_class": self.failure_class,
            "diagnosis": self.diagnosis,
            "diagnosis_evidence": self.diagnosis_evidence,
            "next_actions": self.next_actions,
        }


@dataclass
class BenchRunSummary:
    """Top-level benchmark run state used by the result card."""

    run_id: str
    dataset_name: str
    split: str
    provider: str
    model: str
    output_dir: Path
    predictions_path: Path
    agent_mode: str = "single"
    profile: str = ""
    max_revision_rounds: int = 0
    baseline_predictions_path: Path | None = None
    official_eval_command: list[str] = field(default_factory=list)
    official_eval_exit_code: int | None = None
    official_eval_output: str = ""
    case_results: list[BenchCaseResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize summary data for machine-readable artifacts."""

        return {
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "split": self.split,
            "provider": self.provider,
            "model": self.model,
            "agent_mode": self.agent_mode,
            "profile": self.profile,
            "max_revision_rounds": self.max_revision_rounds,
            "output_dir": str(self.output_dir),
            "predictions_path": str(self.predictions_path),
            "baseline_predictions_path": (
                str(self.baseline_predictions_path) if self.baseline_predictions_path else ""
            ),
            "official_eval_command": self.official_eval_command,
            "official_eval_exit_code": self.official_eval_exit_code,
            "official_eval_output": self.official_eval_output,
            "case_results": [result.to_dict() for result in self.case_results],
            "notes": self.notes,
        }
