"""Compatibility facade for the layered Workbench package."""

from agent_forge.workbench.presentation.http import (
    INDEX_HTML,
    ForgeUiHandler,
    UiCommand,
    UiJob,
    _latest_comparison_path,
    _latest_fanout_summary_path,
    _latest_feedback_outcome,
    _latest_feedback_path,
    _latest_multi_agent_summary_path,
    _latest_report_path,
    _latest_run_dir,
    _latest_trace_path,
    _latest_usage_path,
    _read_latest_report,
    _render_evidence_html,
    _render_result_summary,
    _render_usage_dashboard,
    build_ui_parser,
    run_ui,
    run_ui_from_args,
)
from agent_forge.workbench.adapters.background_jobs import UiState
from agent_forge.workbench.presentation.commands import (
    build_agent_run_command as _build_agent_run_command,
    build_swebench_command as _build_swebench_command,
    build_workbench_command as _action_to_command,
)

__all__ = [
    "INDEX_HTML",
    "ForgeUiHandler",
    "UiCommand",
    "UiJob",
    "UiState",
    "build_ui_parser",
    "run_ui",
    "run_ui_from_args",
]
