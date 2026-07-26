#!/usr/bin/env python3
"""PyCharm 追问按钮：运行双 worker 只读 fanout 并打开 Evidence。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "examples" / "fanout-plan.sample.json"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs"
KEYCHAIN_SERVICE = "NanoHarness DeepSeek API"
sys.path.insert(0, str(PROJECT_ROOT))

from examples.debug_lab.support import load_or_store_deepseek_key  # noqa: E402


def main() -> None:
    """并行运行两个独立 AgentLoop，再用只读 Workbench 展示结果。"""

    from agent_forge.cli.dispatch import main as forge_main

    os.chdir(PROJECT_ROOT)
    load_or_store_deepseek_key(KEYCHAIN_SERVICE)
    forge_main(
        [
            "run",
            "并行审查 Runtime 与 Safety 边界，不修改文件",
            "--workspace",
            str(PROJECT_ROOT),
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
            "8",
            "--approval-mode",
            "locked",
            "--agent-mode",
            "fanout",
            "--fanout-plan",
            str(PLAN_PATH),
            "--max-workers",
            "2",
            "--execution-mode",
            "worktree",
            "--no-keep-worktree",
            "--skills",
            "none",
            "--memory-recall-limit",
            "0",
        ]
    )
    subprocess.run(
        [str(PROJECT_ROOT / "scripts" / "interview_demo.sh"), "--show-latest"],
        cwd=PROJECT_ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
