from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.evaluation.domain.scorecard import build_scorecard, normalize_case
from agent_forge.evaluation.ports.evidence import CaseEvidenceReader


class BuildBenchmarkScorecard:

    def __init__(self, evidence_reader: CaseEvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    # 主要入口：下方定义承接该模块的核心调用。
    def execute(
        self,
        results: dict[str, Any],
        run_dir: str | Path,
    ) -> dict[str, Any]:
        """读取运行证据并构造 claim-safe benchmark scorecard。"""

        root = Path(run_dir)
        raw_cases = results.get("case_results")
        case_results = raw_cases if isinstance(raw_cases, list) else []
        normalized_cases = [
            normalize_case(
                item,
                usage=self._evidence_reader.load_usage(item, root),
                environment=self._evidence_reader.load_environment(item, root),
            )
            for item in case_results
            if isinstance(item, dict)
        ]
        return build_scorecard(results, normalized_cases)
