"""Compatibility exports for parsed official evaluator artifacts."""

from .adapters.official_results import (
    OfficialCaseOutcome,
    OfficialResults,
    apply_official_results,
    parse_official_results,
)

__all__ = [
    "OfficialCaseOutcome",
    "OfficialResults",
    "apply_official_results",
    "parse_official_results",
]
