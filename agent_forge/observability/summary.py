"""Compatibility helper for the optional trace summary file."""

from pathlib import Path

from .presentation.trace_summary import render_trace_summary


def write_summary(path: str | Path, trace: dict) -> None:
    Path(path).with_name("summary.md").write_text(
        render_trace_summary(trace),
        encoding="utf-8",
    )


__all__ = ["render_trace_summary", "write_summary"]
