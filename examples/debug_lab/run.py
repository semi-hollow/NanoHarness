#!/usr/bin/env python3
"""固定输入、可重复运行的 NanoHarness 内部调试实验场。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = Path(__file__).resolve().parent / "repository"
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs"
KEYCHAIN_SERVICE = "NanoHarness DeepSeek API"
ASTROPY_INSTANCE = "astropy__astropy-12907"
SWEBENCH_REPOSITORY = "https://github.com/SWE-bench/SWE-bench.git"
SWEBENCH_REVISION = "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
TASK = (
    "修复 calculator.py 的 add：2 + 3 必须等于 5。不要修改测试；"
    "修改后必须调用 diagnostics，kind=pytest，target=test_calculator.py。"
)
sys.path.insert(0, str(PROJECT_ROOT))

from examples.debug_lab.support import (  # noqa: E402
    DeterministicRepairModel,
    artifact_from_pointer,
    create_workspace,
    ensure_docker,
    ensure_swebench,
    load_or_store_deepseek_key,
    publish_latest,
    remember_root_pointer,
    restore_evidence as restore_saved_evidence,
)


def _forge_main(argv: list[str]) -> None:
    from agent_forge.cli.dispatch import main as dispatch_main

    dispatch_main(argv)


def _new_workspace(scenario: str) -> Path:
    return create_workspace(
        scenario,
        template_root=TEMPLATE_ROOT,
        state_root=STATE_ROOT,
    )


def _publish_latest(artifact_dir: Path, *, scenario: str = "") -> None:
    publish_latest(
        artifact_dir,
        project_root=PROJECT_ROOT,
        state_root=STATE_ROOT,
        scenario=scenario,
    )


def _artifact_from_pointer(pointer: Path) -> Path:
    return artifact_from_pointer(pointer)


def _remember_root_pointer(scenario: str, pointer_name: str) -> None:
    remember_root_pointer(
        scenario,
        pointer_name,
        project_root=PROJECT_ROOT,
        state_root=STATE_ROOT,
    )


def restore_evidence(scenario: str) -> None:
    restore_saved_evidence(
        scenario,
        project_root=PROJECT_ROOT,
        state_root=STATE_ROOT,
        runs_root=RUNS_ROOT,
    )


def _load_or_store_deepseek_key() -> None:
    load_or_store_deepseek_key(KEYCHAIN_SERVICE)


def _ensure_docker() -> None:
    ensure_docker()


def _ensure_swebench() -> None:
    ensure_swebench(
        project_root=PROJECT_ROOT,
        state_root=STATE_ROOT,
        repository=SWEBENCH_REPOSITORY,
        revision=SWEBENCH_REVISION,
    )


def run_control() -> None:
    from agent_forge.showcase import run_governed_demo

    print("LAB 1/4: approval -> checkpoint -> continuation")
    result = run_governed_demo("approval", output_root=RUNS_ROOT)
    _publish_latest(result.inspect_target, scenario="control")
    print(f"STATUS: {result.waiting_status} -> {result.completed_status}")
    print(f"ARTIFACT: {result.inspect_target}")


def run_fixed() -> None:
    from agent_forge import Harness, HarnessConfig

    workspace = _new_workspace("fixed")
    print(
        "LAB 2/4: read -> read -> patch -> pytest -> final\n"
        f"FIXED INPUT: {workspace}"
    )
    result = Harness(
        model=DeterministicRepairModel(),
        config=HarnessConfig(
            workspace=str(workspace),
            output_root=str(RUNS_ROOT),
            max_steps=6,
            approval_mode="on-write",
            auto_approve_writes=True,
            enabled_tools=("read_file", "apply_patch", "diagnostics"),
            tool_routing_mode="all",
            skill_mode="none",
            memory_recall_limit=0,
        ),
    ).run(TASK)
    _publish_latest(result.artifact_dir, scenario="fixed")
    print(f"STATUS: {result.status.value}\nARTIFACT: {result.artifact_dir}")


def run_live() -> None:
    _load_or_store_deepseek_key()
    workspace = _new_workspace("live")
    print(f"LAB 3/4: real DeepSeek, same input\nFIXED INPUT: {workspace}")
    _forge_main(
        [
            "run",
            TASK,
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
            "8",
            "--approval-mode",
            "on-write",
            "--auto-approve-writes",
            "--tool-routing",
            "all",
            "--skills",
            "none",
            "--memory-recall-limit",
            "0",
            "--tool",
            "read_file",
            "--tool",
            "apply_patch",
            "--tool",
            "diagnostics",
        ]
    )
    artifact = _artifact_from_pointer(workspace / ".agent_forge" / "latest" / "run.txt")
    _publish_latest(artifact, scenario="live")


def run_astropy() -> None:
    _load_or_store_deepseek_key()
    _ensure_docker()
    _ensure_swebench()
    print(f"LAB 4/4: {ASTROPY_INSTANCE} -> local evidence -> official oracle")
    _forge_main(
        [
            "bench",
            "swebench",
            "--instance-id",
            ASTROPY_INSTANCE,
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
            "--output-root",
            str(RUNS_ROOT),
        ]
    )
    _remember_root_pointer("astropy", "bench.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        choices=(
            "control",
            "fixed",
            "live",
            "astropy",
            "show-live",
            "show-astropy",
        ),
    )
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)
    {
        "control": run_control,
        "fixed": run_fixed,
        "live": run_live,
        "astropy": run_astropy,
        "show-live": lambda: restore_evidence("live"),
        "show-astropy": lambda: restore_evidence("astropy"),
    }[args.scenario]()


if __name__ == "__main__":
    main()
