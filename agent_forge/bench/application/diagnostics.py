from __future__ import annotations

from agent_forge.bench.domain.failure_taxonomy import (
    FailureDiagnosis,
    classify_case_result,
)
from agent_forge.bench.domain.models import BenchCaseResult
from agent_forge.bench.ports.benchmark import CaseEvidenceReader


class DiagnoseBenchCase:

    def __init__(self, evidence_reader: CaseEvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    # 主要入口：下方定义承接该模块的核心调用。
    def attach(self, result: BenchCaseResult) -> BenchCaseResult:
        """把最终证据支持的唯一失败诊断写回 case result。"""

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
