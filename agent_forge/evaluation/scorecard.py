"""Compatibility exports for benchmark scorecards."""

from .api import (
    build_benchmark_scorecard,
    load_benchmark_scorecard,
    write_benchmark_scorecard,
)
from .presentation.scorecard_report import render_benchmark_scorecard

__all__ = [
    "build_benchmark_scorecard",
    "load_benchmark_scorecard",
    "render_benchmark_scorecard",
    "write_benchmark_scorecard",
]
