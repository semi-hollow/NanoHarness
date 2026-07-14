"""Compatibility exports for paired ablation experiments."""

from .api import write_ablation_comparison
from .domain.ablation import compare_benchmark_scorecards
from .presentation.ablation_report import render_ablation_report

__all__ = [
    "compare_benchmark_scorecards",
    "render_ablation_report",
    "write_ablation_comparison",
]
