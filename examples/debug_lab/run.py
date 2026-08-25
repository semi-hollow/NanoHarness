#!/usr/bin/env python3
"""运行 Durable Control Lab，或只读打开统一 Evidence Workbench。"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from agent_forge.infrastructure.storage_layout import ensure_storage_layout
from apps.showcase import ControlPlaneShowcaseResult
from examples.debug_lab.support import publish_latest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "internal" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs" / "showcases"
WORKBENCH_LAUNCHER = PROJECT_ROOT / "scripts" / "showcase_demo.sh"


def _publish_latest(artifact_dir: Path) -> None:
    publish_latest(
        artifact_dir,
        project_root=PROJECT_ROOT,
        state_root=STATE_ROOT,
        scenario="control",
    )


def _open_workbench(*, stay_attached: bool = False) -> None:
    """复用统一启动器；本入口不加工 Runtime evidence。"""

    flag = "--serve" if stay_attached else "--show-governed"
    try:
        subprocess.run([str(WORKBENCH_LAUNCHER), flag], cwd=PROJECT_ROOT, check=True)
    except KeyboardInterrupt:
        if not stay_attached:
            raise
        print("\n只读 Workbench 已停止。")


def open_existing_evidence_workbench() -> None:
    print(
        "WORKBENCH ONLY: 不运行 Agent、不调用模型；"
        "页面内可选择 Durable Control、Multi-Agent Runtime 和 Benchmark evidence。"
    )
    _open_workbench(stay_attached=True)


def run_governed(*, interactive: bool = False, open_workbench: bool = True) -> None:
    """运行唯一交互 Lab；按钮动作可与 durable JSON 变化逐步对照。"""

    from apps.showcase import run_governed_demo

    def publish_and_open(result: ControlPlaneShowcaseResult) -> None:
        _publish_latest(result.artifact_dir)
        if open_workbench:
            _open_workbench()

    if interactive:
        from apps.showcase.console import run_governed_showcase_console

        run_governed_showcase_console(
            output_root=RUNS_ROOT,
            open_workbench=publish_and_open,
        )
        return

    print(
        "LAB: human choice -> checkpoint -> patch approval -> continuation "
        "-> focused pytest -> evidence"
    )
    result = run_governed_demo("governed", output_root=RUNS_ROOT)
    _publish_latest(result.inspect_target)
    print("STATUS: " + " -> ".join(result.state_sequence))
    print(f"ARTIFACT: {result.inspect_target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("governed", "workbench"))
    parser.add_argument(
        "--open-workbench",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)
    ensure_storage_layout(PROJECT_ROOT)

    if args.scenario == "workbench":
        open_existing_evidence_workbench()
        return
    should_open = True if args.open_workbench is None else args.open_workbench
    run_governed(interactive=args.interactive, open_workbench=should_open)
    if not args.interactive and should_open:
        _open_workbench()


if __name__ == "__main__":
    main()
