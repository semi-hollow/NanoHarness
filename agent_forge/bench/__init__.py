"""SWE-bench-oriented evaluation layer.

This package replaces the old repo-local eval cases. The goal is not to prove
the runtime with author-created puzzles; it is to run the same shape of task
used by public CodingAgent benchmarks: real GitHub issue, real repository,
base commit checkout, generated patch, official harness evaluation.
"""

from .types import BenchCase, BenchCaseResult, BenchRunSummary

__all__ = ["BenchCase", "BenchCaseResult", "BenchRunSummary"]
