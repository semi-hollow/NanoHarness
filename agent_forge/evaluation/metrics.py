"""Compatibility exports for run-metric projection."""

from .adapters.json_files import load_json_if_exists
from .domain.run_metrics import extract_run_metrics

__all__ = ["extract_run_metrics", "load_json_if_exists"]
