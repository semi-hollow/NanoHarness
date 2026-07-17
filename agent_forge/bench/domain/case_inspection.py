"""Benchmark Case Explorer 的只读领域数据。

阅读顺序：``BenchmarkSetProfile`` 解释为什么选择一组 case，
``BenchmarkCaseProfile`` 解释为什么选择某一道题，``BenchmarkCaseInspection``
保存该题实际输入与验收契约。这里不加载数据集、不运行 Agent，
也不渲染页面。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# 核心数据：固定回归集中一道题的人工选择语义。
@dataclass(frozen=True)
class BenchmarkCaseProfile:
    """项目为固定回归 case 补充的人类可读语义。

    字段说明：

    - ``instance_id``：数据集稳定主键，也是 ``forge bench case`` 查询键。
    - ``title``：供目录快速扫描的标题；``issue_type``：该题的问题族。
    - ``summary``：问题的一句话说明，不包含参考答案。
    - ``harness_signals``：该题可以观察的 Harness 能力维度。
    - ``selection_reason``：它为什么进入固定回归集，而不是为什么容易通过。
    """

    instance_id: str
    title: str
    issue_type: str
    summary: str
    harness_signals: tuple[str, ...]
    selection_reason: str

    def to_dict(self) -> dict[str, Any]:
        """转换为 CLI JSON 可以直接序列化的结构。"""

        return {
            "instance_id": self.instance_id,
            "title": self.title,
            "issue_type": self.issue_type,
            "summary": self.summary,
            "harness_signals": list(self.harness_signals),
            "selection_reason": self.selection_reason,
        }


# 核心数据：固定回归集合的选择方法、覆盖目标与结论边界。
@dataclass(frozen=True)
class BenchmarkSetProfile:
    """一个固定回归集合的选择契约和结论边界。

    字段说明：

    - ``name``：集合名；``dataset_name`` 与 ``split``：候选数据集身份。
    - ``universe_case_count``：选择前候选全集规模，不是本次执行数量。
    - ``objective``：集合要验证什么；``selection_method``：如何从全集选题。
    - ``selection_constraints``：每题必须满足的硬约束。
    - ``coverage_dimensions``：五题共同覆盖的 Harness 问题维度。
    - ``claim_limits``：这些结果明确不能支持哪些外推结论。
    """

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
        """转换为 CLI JSON 可以直接序列化的结构。"""

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


# 核心数据：参考答案的规模摘要，仅供运行结束后的人工复盘。
@dataclass(frozen=True)
class PatchSummary:
    """只用于人工复盘的参考 patch 规模，不参与 Agent 输入。

    ``files`` 是涉及文件；``hunks`` 是 diff hunk 数；``additions`` 和
    ``deletions`` 是参考 patch 行数。它只描述规模，不证明 candidate patch 正确。
    """

    files: tuple[str, ...]
    hunks: int
    additions: int
    deletions: int

    def to_dict(self) -> dict[str, Any]:
        """转换为 CLI JSON 可以直接序列化的结构。"""

        return {
            "files": list(self.files),
            "hunks": self.hunks,
            "additions": self.additions,
            "deletions": self.deletions,
        }


# 核心数据：单题模型输入、测试义务和默认隐藏的官方复盘材料。
@dataclass(frozen=True)
class BenchmarkCaseInspection:
    """一个 case 的问题输入、测试契约和受控复盘材料。

    字段说明：

    - ``instance_id``、``repo``、``base_commit``、``version``：可复现代码起点。
    - ``problem_statement``、``hints_text``：数据集给 Agent 的任务输入。
    - ``fail_to_pass``：修复前失败、修复后必须通过的目标测试。
    - ``pass_to_pass``：修复前已通过、修复后仍必须通过的回归测试。
    - ``profile``：本项目补充的选题语义；非固定集合 case 可以为空。
    - ``test_patch``：官方测试补丁；默认禁止输出到 Agent 或普通目录页。
    - ``gold_patch``、``gold_patch_summary``：参考答案及规模，只能显式复盘查看。
    """

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
        """按显式开关序列化；默认不返回 test patch 或 gold patch。"""

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
