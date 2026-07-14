"""Multi-Agent 报告渲染。"""

from .live_report import render_live_fanout_report
from .report import render_multi_agent_report

__all__ = ["render_live_fanout_report", "render_multi_agent_report"]
