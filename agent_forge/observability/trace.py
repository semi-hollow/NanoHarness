"""Compatibility exports for JSON trace recording."""

from .adapters.json_trace import JsonTraceRecorder, TraceRecorder

__all__ = ["JsonTraceRecorder", "TraceRecorder"]
