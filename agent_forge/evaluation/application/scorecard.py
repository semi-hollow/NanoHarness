from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.evaluation.domain.scorecard import build_scorecard, normalize_case
from agent_forge.evaluation.ports.evidence import CaseEvidenceReader


class BuildBenchmarkScorecard:
    def __init__(self, evidence_reader: CaseEvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    # 主要入口：读取 case evidence，归一化后聚合为 claim-safe scorecard。
    def build_scorecard(
        self,
        results: dict[str, Any],
        run_dir: str | Path,
    ) -> dict[str, Any]:
        """读取运行证据并构造 claim-safe benchmark scorecard。"""

        run_directory = Path(run_dir)
        raw_case_results = results.get("case_results")
        case_results = raw_case_results if isinstance(raw_case_results, list) else []
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
