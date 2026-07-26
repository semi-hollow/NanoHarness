#!/usr/bin/env python3
"""PyCharm 面试按钮：复用 interview_demo.sh 打开对应 Evidence。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "scripts" / "interview_demo.sh"
SCRIPT_ARGUMENTS = {
    "control": [],
    "latest": ["--show-latest"],
    "live": ["--show-live"],
    "official": ["--show-astropy"],
}


def main() -> None:
    """将短场景名映射到已验证的演示编排脚本。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=tuple(SCRIPT_ARGUMENTS))
    args = parser.parse_args()
    subprocess.run(
        [str(DEMO_SCRIPT), *SCRIPT_ARGUMENTS[args.scenario]],
        cwd=PROJECT_ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
