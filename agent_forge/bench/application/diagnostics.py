from __future__ import annotations

from agent_forge.bench.domain.failure_taxonomy import (
    FailureDiagnosis,
    classify_case_result,
)
from agent_forge.bench.domain.models import BenchCaseResult
from agent_forge.bench.ports.benchmark import CaseEvidenceReader


class DiagnoseBenchCase:
    """Classify a final case only after all evaluation evidence is available."""

    def __init__(self, evidence_reader: CaseEvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    # PRIMARY ENTRYPOINT: attach one final, evidence-backed failure diagnosis.
    def attach(self, result: BenchCaseResult) -> BenchCaseResult:
        """Mutate the result once so every later artifact sees one diagnosis."""

        diagnosis = self.diagnose(result)
        result.failure_class = diagnosis.failure_class
        result.diagnosis = diagnosis.summary
        result.diagnosis_evidence = diagnosis.evidence
        result.next_actions = diagnosis.next_actions
        return result

    def diagnose(self, result: BenchCaseResult) -> FailureDiagnosis:
        return classify_case_result(
            result,
            self._evidence_reader.load_usage(result),
            self._evidence_reader.load_trace(result),
        )
