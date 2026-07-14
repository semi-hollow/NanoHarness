from __future__ import annotations

from typing import Any

from agent_forge.observability.domain.usage import build_usage_report


class BuildUsageReport:

    # 主要入口：下方定义承接该模块的核心调用。
    def execute(self, trace: dict[str, Any]) -> dict[str, Any]:
        """把 trace 事实投影为稳定 usage 读模型。"""

        return build_usage_report(trace)
