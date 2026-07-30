#!/usr/bin/env python3
"""PyCharm 面试按钮：运行确定性闭环并打开同一份只读 Evidence。"""

from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "scripts" / "interview_demo.sh"
def main() -> None:
    """复用唯一面试脚本，避免多个展示入口逐步漂移。"""

    subprocess.run(
        [str(DEMO_SCRIPT)],
        cwd=PROJECT_ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
