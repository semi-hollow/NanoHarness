#!/usr/bin/env python3
"""固定输入、可重复运行的 NanoHarness 内部调试实验场。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MULTI_AGENT_TEMPLATE_ROOT = Path(__file__).resolve().parent / "multi_agent_repository"
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "internal" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs" / "showcases"
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
from agent_forge.showcase import ControlPlaneShowcaseResult  # noqa: E402
from agent_forge.storage_layout import ensure_storage_layout  # noqa: E402
from agent_forge.tools.registry import ToolRegistry  # noqa: E402


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


def run_governed(
    *,
    interactive: bool = False,
    open_workbench: bool = True,
) -> None:
    """运行 Lab 1；正式 IDE 入口使用按钮控制台，自动化保留 headless 路径。"""

    from agent_forge.showcase import run_governed_demo

    def publish_and_open(result: ControlPlaneShowcaseResult) -> None:
        _publish_latest(result.artifact_dir, scenario="control")
        if open_workbench:
            _open_published_evidence_in_workbench("governed")

    if interactive:
        from agent_forge.showcase.console import run_governed_showcase_console

        run_governed_showcase_console(
            output_root=RUNS_ROOT,
            open_workbench=publish_and_open,
        )
        return

    print(
        "LAB 1/3: human choice -> checkpoint -> patch approval -> continuation "
        "-> focused pytest -> evidence"
    )
    governed_demo_result = run_governed_demo("governed", output_root=RUNS_ROOT)
    _publish_latest(governed_demo_result.inspect_target, scenario="control")
    print("STATUS: " + " -> ".join(governed_demo_result.state_sequence))
    print(f"ARTIFACT: {governed_demo_result.inspect_target}")


def run_coordinated() -> None:
    """运行两个并行策略 Worker、依赖验证 Worker 和只读 Finalizer。"""

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
    (run_dir / "scenario_contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "debug_lab_scenario_contract",
                "scenario": "coordinated",
                "title": "Checkout policy integration and abnormal-input matrix",
                "cases": [
                    {
                        "case": "normal checkout",
                        "expected": "discount and standard domestic fee compose to 85",
                        "owner": "pricing-policy + shipping-policy",
                    },
                    {
                        "case": "invalid subtotal or discount",
                        "expected": "fail closed with ValueError",
                        "owner": "pricing-policy",
                    },
                    {
                        "case": "free-shipping threshold",
                        "expected": "standard domestic fee becomes zero at 100",
                        "owner": "shipping-policy",
                    },
                    {
                        "case": "expedited threshold order",
                        "expected": "expedited fee remains 15 instead of becoming free",
                        "owner": "shipping-policy",
                    },
                    {
                        "case": "unknown shipping region",
                        "expected": "reject rather than silently defaulting to zero",
                        "owner": "shipping-policy",
                    },
                ],
                "integration_gate": (
                    "edge-case-verifier depends on both policy workers and cannot run "
                    "until both scoped diffs are merged"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    trace = TraceRecorder(str(run_dir / "trace.json"))
    plan = FanoutPlan.from_mapping(
        {
            "goal": (
                "Repair checkout pricing and shipping policies, reject invalid inputs, "
                "preserve expedited-fee semantics, then verify all edge cases."
            ),
            "tasks": [
                {
                    "id": "pricing-policy",
                    "task": (
                        "Fix discount calculation in pricing.py and fail closed for "
                        "negative subtotal, negative discount, or discount above subtotal."
                    ),
                    "write_scope": ["pricing.py"],
                    "allowed_tools": ["read_file", "replace_text", "git_diff"],
                    "expected_artifact": "pricing_policy_result",
                    "max_steps": 5,
                },
                {
                    "id": "shipping-policy",
                    "task": (
                        "Fix shipping.py so standard, expedited, international, and "
                        "unsupported-region branches have explicit behavior."
                    ),
                    "write_scope": ["shipping.py"],
                    "allowed_tools": ["read_file", "replace_text", "git_diff"],
                    "expected_artifact": "shipping_policy_result",
                    "max_steps": 5,
                },
                {
                    "id": "edge-case-verifier",
                    "task": (
                        "After both policies are integrated, run test_checkout.py and "
                        "verify invalid pricing, free-shipping threshold, expedited "
                        "shipping, and unknown-region behavior."
                    ),
                    "depends_on": ["pricing-policy", "shipping-policy"],
                    "write_scope": [],
                    "allowed_tools": ["python_validation", "git_diff"],
                    "expected_artifact": "edge_case_verification",
                    "max_steps": 4,
                },
            ],
        }
    )
    trace.set_run_context(task=plan.goal)

    def registry_factory(
        worktree: Path,
        environment: ExecutionEnvironment,
    ) -> ToolRegistry:
        return build_registry(
            ToolRegistryBuildRequest(
                workspace=str(worktree),
                auto=True,
                execution_environment=environment,
            )
        )

    print(
        "LAB 2/3: parallel policy workers -> dependency gate -> edge-case verifier "
        "-> scoped merge -> read-only finalizer"
    )
    fanout_summary = build_live_fanout(
        LiveFanoutBuildRequest(
            plan=plan,
            base_config=RuntimeConfig(
                workspace=str(workspace),
                max_steps=6,
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
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="为 Lab 1 打开按钮式 HITL/审批控制台；其他场景忽略。",
    )
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)
    ensure_storage_layout(PROJECT_ROOT)

    # 只读入口与三个产证 Lab 分开：复盘历史运行不应再次消耗模型 token。
    if args.scenario == "workbench":
        open_existing_evidence_workbench()
        return

    should_open_workbench = (
        args.scenario in WORKBENCH_FLAGS
        if args.open_workbench is None
        else args.open_workbench
    )
    if args.scenario == "governed":
        run_governed(
            interactive=args.interactive,
            open_workbench=should_open_workbench,
        )
        if not args.interactive and should_open_workbench:
            _open_published_evidence_in_workbench("governed")
        return

    run_coordinated()
    if should_open_workbench:
        _open_published_evidence_in_workbench(args.scenario)


if __name__ == "__main__":
    main()
