from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BenchmarkCaseProfile:
    """项目为固定回归 case 补充的人类可读语义。"""

    instance_id: str
    title: str
    issue_type: str
    summary: str
    harness_signals: tuple[str, ...]
    selection_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "title": self.title,
            "issue_type": self.issue_type,
            "summary": self.summary,
            "harness_signals": list(self.harness_signals),
            "selection_reason": self.selection_reason,
        }


@dataclass(frozen=True)
class BenchmarkSetProfile:
    """一个固定回归集合的选择契约和结论边界。"""

    name: str
    dataset_name: str
    split: str
    universe_case_count: int
    objective: str
    selection_method: str
    selection_constraints: tuple[str, ...]
    coverage_dimensions: tuple[str, ...]
    claim_limits: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dataset_name": self.dataset_name,
            "split": self.split,
            "universe_case_count": self.universe_case_count,
            "objective": self.objective,
            "selection_method": self.selection_method,
            "selection_constraints": list(self.selection_constraints),
            "coverage_dimensions": list(self.coverage_dimensions),
            "claim_limits": list(self.claim_limits),
        }


@dataclass(frozen=True)
class PatchSummary:
    """只用于人工复盘的参考 patch 规模，不参与 Agent 输入。"""

    files: tuple[str, ...]
    hunks: int
    additions: int
    deletions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "hunks": self.hunks,
            "additions": self.additions,
            "deletions": self.deletions,
        }


@dataclass(frozen=True)
class BenchmarkCaseInspection:
    """一个 case 的问题输入、测试契约和受控复盘材料。"""

    instance_id: str
    repo: str
    base_commit: str
    version: str
    problem_statement: str
    hints_text: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    profile: BenchmarkCaseProfile | None
    test_patch: str
    gold_patch: str
    gold_patch_summary: PatchSummary

    def to_dict(
        self,
        *,
        include_test_patch: bool = False,
        include_gold_patch: bool = False,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "version": self.version,
            "problem_statement": self.problem_statement,
            "hints_text": self.hints_text,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
            "profile": self.profile.to_dict() if self.profile else None,
            "test_patch_visible": include_test_patch,
            "gold_patch_visible": include_gold_patch,
        }
        if include_test_patch:
            data["test_patch"] = self.test_patch
        if include_gold_patch:
            data["gold_patch_summary"] = self.gold_patch_summary.to_dict()
            data["gold_patch"] = self.gold_patch
        return data
