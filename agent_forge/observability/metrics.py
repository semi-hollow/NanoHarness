"""Compatibility exports for trace metric projection."""

from .api import summarize_trace_file
from .domain.metrics import summarize, summarize_trace

__all__ = ["summarize", "summarize_trace", "summarize_trace_file"]
