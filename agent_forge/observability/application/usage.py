from __future__ import annotations

from typing import Any

from agent_forge.observability.domain.usage import build_usage_report


class BuildUsageReport:
    """Project an immutable trace fact stream into usage and control metrics."""

    # PRIMARY ENTRYPOINT: build the stable usage read model.
    def execute(self, trace: dict[str, Any]) -> dict[str, Any]:
        """Return a rebuildable read model without file or rendering concerns."""

        return build_usage_report(trace)
