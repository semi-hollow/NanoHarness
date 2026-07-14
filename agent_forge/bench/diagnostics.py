"""Compatibility facade for final benchmark diagnosis."""

from .adapters.case_evidence import JsonCaseEvidenceReader
from .application.diagnostics import DiagnoseBenchCase
from .domain.failure_taxonomy import FailureDiagnosis
from .domain.models import BenchCaseResult


def attach_failure_diagnosis(result: BenchCaseResult) -> BenchCaseResult:
    return DiagnoseBenchCase(JsonCaseEvidenceReader()).attach(result)


def diagnose_case_result(result: BenchCaseResult) -> FailureDiagnosis:
    return DiagnoseBenchCase(JsonCaseEvidenceReader()).diagnose(result)


__all__ = ["attach_failure_diagnosis", "diagnose_case_result"]
