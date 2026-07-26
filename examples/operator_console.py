#!/usr/bin/env python3
"""PyCharm 一键启动的真实模型 Operator Console。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = PROJECT_ROOT / "examples" / "debug_lab" / "repository"
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs"
KEYCHAIN_SERVICE = "NanoHarness DeepSeek API"
DEFAULT_TASK = (
    "先阅读 calculator.py 和 test_calculator.py。修改前必须使用 ask_human "
    "询问我目标 Python 版本；得到回答后修复 add，使 2 + 3 等于 5，"
    "最后调用 diagnostics 运行 test_calculator.py。"
)
sys.path.insert(0, str(PROJECT_ROOT))

from examples.debug_lab.support import (  # noqa: E402
    artifact_from_pointer,
    create_workspace,
    load_or_store_deepseek_key,
    publish_latest,
)


def main() -> None:
    """准备安全练习仓库和 API Key，再进入真实 TUI。"""

    from agent_forge.cli.dispatch import main as forge_main

    os.chdir(PROJECT_ROOT)
    load_or_store_deepseek_key(KEYCHAIN_SERVICE)
    workspace = create_workspace(
        "console",
        template_root=TEMPLATE_ROOT,
        state_root=STATE_ROOT,
    )
    forge_main(
        [
            "console",
            DEFAULT_TASK,
            "--workspace",
            str(workspace),
            "--output-root",
            str(RUNS_ROOT),
            "--provider",
            "deepseek",
            "--model",
            "deepseek-v4-pro",
            "--thinking",
            "enabled",
            "--reasoning-effort",
            "max",
            "--max-steps",
            "10",
            "--approval-mode",
            "on-write",
            "--no-auto-approve-writes",
            "--tool-routing",
            "all",
            "--skills",
            "none",
            "--memory-recall-limit",
            "0",
        ]
    )
    workspace_pointer = workspace / ".agent_forge" / "latest" / "run.txt"
    if workspace_pointer.is_file():
        publish_latest(
            artifact_from_pointer(workspace_pointer),
            project_root=PROJECT_ROOT,
            state_root=STATE_ROOT,
            scenario="live",
        )


if __name__ == "__main__":
    main()
