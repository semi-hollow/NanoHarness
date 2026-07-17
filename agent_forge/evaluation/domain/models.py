from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 核心数据：同一任务 single/multi 的成本、patch、评审与失败分类对照。
@dataclass
class EvaluationComparison:
    """``compare_runs`` 的稳定结果，区分 Runtime 状态和 patch 是否生成。

    single/multi 前缀字段分别保存状态、candidate patch、成本和调用计数；
    revision/reviewer/verifier 字段保存多角色证据；taxonomy 与 recommendation 给出
    保守结论，不把任一 Agent 完成状态等同于 official resolved。
    """

    task_id: str
    single_status: str = ""
    multi_status: str = ""
    single_patch_generated: bool = False
    multi_patch_generated: bool = False
    single_cost_usd: float = 0.0
    multi_cost_usd: float = 0.0
    single_llm_calls: int = 0
    multi_llm_calls: int = 0
    single_tool_calls: int = 0
    multi_tool_calls: int = 0
    single_failed_tool_calls: int = 0
    multi_failed_tool_calls: int = 0
    revision_rounds: int = 0
    reviewer_findings: list[str] = field(default_factory=list)
    verifier_status: str = ""
    failure_taxonomy: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:

        return {
            "task_id": self.task_id,
            "single_status": self.single_status,
            "multi_status": self.multi_status,
            "single_patch_generated": self.single_patch_generated,
            "multi_patch_generated": self.multi_patch_generated,
            "single_cost_usd": self.single_cost_usd,
            "multi_cost_usd": self.multi_cost_usd,
            "single_llm_calls": self.single_llm_calls,
            "multi_llm_calls": self.multi_llm_calls,
            "single_tool_calls": self.single_tool_calls,
            "multi_tool_calls": self.multi_tool_calls,
            "single_failed_tool_calls": self.single_failed_tool_calls,
            "multi_failed_tool_calls": self.multi_failed_tool_calls,
            "revision_rounds": self.revision_rounds,
            "reviewer_findings": self.reviewer_findings,
            "verifier_status": self.verifier_status,
            "failure_taxonomy": self.failure_taxonomy,
            "recommendation": self.recommendation,
        }
