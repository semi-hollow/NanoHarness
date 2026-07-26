#!/usr/bin/env python3
"""PyCharm 一键运行可复核的 SWE-bench Verified Smoke-5 campaign。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "debug-lab"
KEYCHAIN_SERVICE = "NanoHarness DeepSeek API"
DATASET = "SWE-bench/SWE-bench_Verified"
SWEBENCH_REPOSITORY = "https://github.com/SWE-bench/SWE-bench.git"
SWEBENCH_REVISION = "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
sys.path.insert(0, str(PROJECT_ROOT))

from examples.debug_lab.support import (  # noqa: E402
    ensure_docker,
    ensure_swebench,
    load_or_store_deepseek_key,
)


# 主要入口：准备 provider/official evaluator 后执行 5 case x 2 preset。
def main() -> None:
    """运行一次低成本证据 campaign；dirty source 会在 artifact 中明确记录。"""

    from agent_forge.cli.dispatch import main as forge_main

    os.chdir(PROJECT_ROOT)
    load_or_store_deepseek_key(KEYCHAIN_SERVICE)
    ensure_docker()
    ensure_swebench(
        project_root=PROJECT_ROOT,
        state_root=STATE_ROOT,
        repository=SWEBENCH_REPOSITORY,
        revision=SWEBENCH_REVISION,
    )
    print(
        "SWE-bench Verified Smoke-5: "
        "5 cases x 2 runtime presets x 1 repetition = 10 official runs"
    )
    forge_main(
        [
            "bench",
            "campaign",
            "--regression-set",
            "smoke-5",
            "--dataset",
            DATASET,
            "--repetitions",
            "1",
            "--provider",
            "deepseek",
            "--model",
            "deepseek-v4-pro",
            "--thinking",
            "enabled",
            "--reasoning-effort",
            "max",
            "--max-steps",
            "16",
            "--timeout-seconds",
            "900",
            "--evaluate",
            "--max-workers",
            "1",
            "--official-cache-level",
            "instance",
            "--allow-dirty",
        ]
    )


if __name__ == "__main__":
    main()
