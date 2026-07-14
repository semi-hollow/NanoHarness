from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.evaluation.domain.scorecard import build_scorecard, normalize_case
from agent_forge.evaluation.ports.evidence import CaseEvidenceReader


class BuildBenchmarkScorecard:
    """Build the claim-safe benchmark read model from raw results and evidence."""

    def __init__(self, evidence_reader: CaseEvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    # PRIMARY ENTRYPOINT: normalize evidence and aggregate one benchmark run.
    def execute(
        self,
        results: dict[str, Any],
        run_dir: str | Path,
    ) -> dict[str, Any]:
        """Return a scorecard while keeping artifact lookup outside Domain."""

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
