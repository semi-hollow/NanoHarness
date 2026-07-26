#!/usr/bin/env python3
"""PyCharm 一键输出最近一次 NanoHarness Evidence。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    from agent_forge.cli.dispatch import main as forge_main

    os.chdir(PROJECT_ROOT)
    forge_main(["inspect", "latest"])


if __name__ == "__main__":
    main()
