from __future__ import annotations

from agent_forge.evaluation.adapters.json_files import JsonCaseEvidenceReader
from agent_forge.evaluation.application.scorecard import BuildBenchmarkScorecard


def build_scorecard_use_case() -> BuildBenchmarkScorecard:
    """Compose the scorecard use case with filesystem evidence adapters."""

    return BuildBenchmarkScorecard(JsonCaseEvidenceReader())
