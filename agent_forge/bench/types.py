"""Compatibility exports; new code should import ``bench.api`` or Domain."""

from .domain.models import BenchCase, BenchCaseResult, BenchRunSummary

__all__ = ["BenchCase", "BenchCaseResult", "BenchRunSummary"]
