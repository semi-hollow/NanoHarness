#!/usr/bin/env python3
"""固定输入、可重复运行的 NanoHarness 内部调试实验场。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MULTI_AGENT_TEMPLATE_ROOT = Path(__file__).resolve().parent / "multi_agent_repository"
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs"
WORKBENCH_LAUNCHER = PROJECT_ROOT / "scripts" / "showcase_demo.sh"
WORKBENCH_FLAGS = {
    "governed": "--show-governed",
    "coordinated": "--show-coordinated",
    "complex": "--show-complex",
}
sys.path.insert(0, str(PROJECT_ROOT))

from examples.debug_lab.support import (  # noqa: E402
    DeterministicFanoutModel,
    create_workspace,
    publish_latest,
)


def _publish_latest(artifact_dir: Path, *, scenario: str = "") -> None:
    publish_latest(
        artifact_dir,
        project_root=PROJECT_ROOT,
        state_root=STATE_ROOT,
        scenario=scenario,
    )


def _open_published_evidence_in_workbench(
    scenario: str,
    *,
    stay_attached: bool = False,
) -> None:
    """复用统一启动器，打开当前 Lab 对应的只读 Evidence 场景。

    ``stay_attached`` 只给独立 Workbench 配置使用：PyCharm 持有服务进程，
    点击停止即可关闭；三个 Lab 完成后仍只负责弹出已有的后台服务。
    """

    try:
        show_flag = WORKBENCH_FLAGS[scenario]
    except KeyError as exc:
        raise ValueError(f"场景不支持自动打开 Workbench: {scenario}") from exc
    if stay_attached:
        if scenario != "complex":
            raise ValueError("当前只有复杂任务 Evidence 支持独立前台 Workbench")
        show_flag = "--serve-complex"
    try:
        subprocess.run(
            [str(WORKBENCH_LAUNCHER), show_flag],
            cwd=PROJECT_ROOT,
            check=True,
        )
    except KeyboardInterrupt:
        if not stay_attached:
            raise
        print("\n只读 Workbench 已停止。")


def open_existing_evidence_workbench() -> None:
    """只读打开最近一次 Lab 3 Evidence，不重新执行 Agent。"""

    print(
        "WORKBENCH ONLY: 不运行 Agent、不调用模型；"
        "默认打开最近一次 Lab 3，页面内可切换其他已发布证据。"
    )
    _open_published_evidence_in_workbench("complex", stay_attached=True)


def run_governed() -> None:
    from agent_forge.showcase import run_governed_demo

    print(
        "LAB 1/3: approval -> checkpoint -> continuation -> write "
        "-> focused pytest -> evidence"
    )
    governed_demo_result = run_governed_demo("approval", output_root=RUNS_ROOT)
    _publish_latest(governed_demo_result.inspect_target, scenario="control")
    print(
        f"STATUS: {governed_demo_result.waiting_status} "
        f"-> {governed_demo_result.completed_status}"
    )
    print(f"ARTIFACT: {governed_demo_result.inspect_target}")


def run_coordinated() -> None:
    """运行两个隔离 worker、真实 diff 合并和只读 pytest finalizer。"""

    from agent_forge.multi_agent.domain.live import FanoutPlan
    from agent_forge.multi_agent.wiring import (
        LiveFanoutBuildRequest,
        build_live_fanout,
    )
    from agent_forge.observability.api import TraceRecorder
    from agent_forge.runtime.config import RuntimeConfig
    from agent_forge.runtime.execution_environment import ExecutionEnvironment
    from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry

    workspace = create_workspace(
        "coordinated",
        template_root=MULTI_AGENT_TEMPLATE_ROOT,
        state_root=STATE_ROOT,
    )
    run_dir = RUNS_ROOT / (
        f"debug-fanout-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    trace = TraceRecorder(str(run_dir / "trace.json"))
    plan = FanoutPlan.from_mapping(
        {
            "goal": "Repair independent pricing and shipping modules, then verify checkout.",
            "tasks": [
                {
                    "id": "pricing",
                    "task": "Fix discount calculation in pricing.py.",
                    "write_scope": ["pricing.py"],
                    "allowed_tools": ["replace_text", "git_diff"],
                    "expected_artifact": "pricing_result",
                    "max_steps": 3,
                },
                {
                    "id": "shipping",
                    "task": "Fix the flat shipping fee in shipping.py.",
                    "write_scope": ["shipping.py"],
                    "allowed_tools": ["replace_text", "git_diff"],
                    "expected_artifact": "shipping_result",
                    "max_steps": 3,
                },
            ],
        }
    )
    trace.set_run_context(task=plan.goal)

    def registry_factory(
        worktree: Path,
        environment: ExecutionEnvironment,
    ):
        return build_registry(
            ToolRegistryBuildRequest(
                workspace=str(worktree),
                auto=True,
                execution_environment=environment,
            )
        )

    print(
        "LAB 2/3: parallel workers -> scoped diffs -> deterministic merge "
        "-> read-only pytest finalizer"
    )
    fanout_summary = build_live_fanout(
        LiveFanoutBuildRequest(
            plan=plan,
            base_config=RuntimeConfig(
                workspace=str(workspace),
                max_steps=4,
                auto_approve_writes=True,
                approval_mode="trusted",
                tool_routing_mode="all",
                skill_mode="none",
                memory_recall_limit=0,
            ),
            trace=trace,
            run_dir=run_dir,
            llm_factory=DeterministicFanoutModel,
            registry_factory=registry_factory,
            max_workers=2,
        )
    ).run()
    trace.set_run_context(
        stop_reason=f"fanout_{fanout_summary.status}",
        final_answer=fanout_summary.final_answer,
    )
    trace.write()
    _publish_latest(run_dir, scenario="coordinated")
    print(
        f"STATUS: {fanout_summary.status}\n"
        f"BATCHES: {fanout_summary.batches}\n"
        f"MERGED: {fanout_summary.merged_task_ids}\n"
        f"FINALIZER: {fanout_summary.final_decision}\n"
        f"ARTIFACT: {run_dir}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        choices=(
            "governed",
            "coordinated",
            "workbench",
        ),
    )
    parser.add_argument(
        "--open-workbench",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Lab 完成后启动 Workbench，并打开对应的 Evidence 场景。"
            "三条正式 Lab 默认开启；自动化时可使用 --no-open-workbench。"
        ),
    )
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)

    # 只读入口与三个产证 Lab 分开：复盘历史运行不应再次消耗模型 token。
    if args.scenario == "workbench":
        open_existing_evidence_workbench()
        return

    {
        "governed": run_governed,
        "coordinated": run_coordinated,
    }[args.scenario]()
    should_open_workbench = (
        args.scenario in WORKBENCH_FLAGS
        if args.open_workbench is None
        else args.open_workbench
    )
    if should_open_workbench:
        _open_published_evidence_in_workbench(args.scenario)


if __name__ == "__main__":
    main()
