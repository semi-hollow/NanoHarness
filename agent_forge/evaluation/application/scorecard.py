"""Benchmark results 到 claim-safe scorecard 的 Application 用例。

系统角色：为每个 Case 关联 usage/environment evidence，再交给纯 Domain 归一化与聚合；
缺失证据保持 unknown，不把 local validation 提升为 official correctness。
输入：raw benchmark result + run dir；输出：claim-safe scorecard。
相邻边界：EvidenceReader 只读文件；Domain 定义指标；本 Application 只协调。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.evaluation.domain.scorecard import build_scorecard, normalize_case
from agent_forge.evaluation.ports.evidence import CaseEvidenceReader


class BuildBenchmarkScorecard:
    """协调 EvidenceReader 与纯 Domain 聚合，不拥有 correctness 判定规则。"""

    def __init__(self, evidence_reader: CaseEvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    # 主要入口：读取 case evidence，归一化后聚合为 claim-safe scorecard。
    def build_scorecard(
        self,
        results: dict[str, Any],
        run_dir: str | Path,
    ) -> dict[str, Any]:
        """读取每个 Case 的 usage/environment，再交给 Domain 统一归一化和聚合。

        伪代码：读取 results.case_results → 为每题关联 usage/environment
        → normalize_case() → build_scorecard()。缺失证据保持默认值，不猜测通过。
        """

        run_directory = Path(run_dir)
        raw_case_results = results.get("case_results")
        case_results = raw_case_results if isinstance(raw_case_results, list) else []
        # 每个 Case 独立读取证据；一个 Case 缺失 usage 不影响其他 Case 的归一化。
        normalized_cases = [
            normalize_case(
                case_result,
                usage=self._evidence_reader.load_usage(
                    case_result,
                    run_directory,
                ),
                environment=self._evidence_reader.load_environment(
                    case_result,
                    run_directory,
                ),
            )
            for case_result in case_results
            if isinstance(case_result, dict)
        ]
        return build_scorecard(results, normalized_cases)
