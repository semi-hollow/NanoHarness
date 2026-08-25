"""Lab 1 的本地 Evidence 指针工具；不包含第二套 Runtime。"""

from __future__ import annotations

import os
from pathlib import Path

from agent_forge.infrastructure.storage_layout import INDEX_ROOT


def publish_latest(
    artifact_dir: Path,
    *,
    project_root: Path,
    state_root: Path,
    scenario: str = "",
) -> None:
    """发布只读 Workbench 指针；raw Run 本身保持不可变。"""

    latest_dir = project_root / INDEX_ROOT
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "run.txt").write_text(
        str(artifact_dir.resolve()),
        encoding="utf-8",
    )
    os.utime(artifact_dir, None)
    if scenario == "control":
        state_dir = state_root / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "workbench_source.txt").write_text(
            "governed",
            encoding="utf-8",
        )
