"""Compatibility exports for failure classification policy."""

from .domain.failure_taxonomy import FailureDiagnosis, classify_case_result

__all__ = ["FailureDiagnosis", "classify_case_result"]
