#!/usr/bin/env python3
"""固定输入、可重复运行的 NanoHarness 内部调试实验场。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SINGLE_AGENT_TEMPLATE_ROOT = Path(__file__).resolve().parent / "repository"
MULTI_AGENT_TEMPLATE_ROOT = (
    Path(__file__).resolve().parent / "multi_agent_repository"
)
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs"
KEYCHAIN_SERVICE = "NanoHarness DeepSeek API"
ASTROPY_INSTANCE = "astropy__astropy-12907"
SWEBENCH_REPOSITORY = "https://github.com/SWE-bench/SWE-bench.git"
SWEBENCH_REVISION = "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
PUBLIC_CAMPAIGN_ROOT = (
    PROJECT_ROOT / "benchmarks" / "campaigns" / "verified-commissioning-2-20260726"
)
TASK = (
    "修复 calculator.py 的 add：2 + 3 必须等于 5。不要修改测试；"
    "修改后必须调用 diagnostics，kind=pytest，target=test_calculator.py。"
)
sys.path.insert(0, str(PROJECT_ROOT))

from examples.debug_lab.support import (  # noqa: E402
    DeterministicFanoutModel,
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
        template_root=SINGLE_AGENT_TEMPLATE_ROOT,
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


def run_governed() -> None:
    from agent_forge.showcase import run_governed_demo

    print(
        "LAB 1/3: approval -> checkpoint -> continuation -> write "
        "-> focused pytest -> evidence"
    )
    result = run_governed_demo("approval", output_root=RUNS_ROOT)
    _publish_latest(result.inspect_target, scenario="control")
    print(f"STATUS: {result.waiting_status} -> {result.completed_status}")
    print(f"ARTIFACT: {result.inspect_target}")


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
    summary = build_live_fanout(
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
        stop_reason=f"fanout_{summary.status}",
        final_answer=summary.final_answer,
    )
    trace.write()
    _publish_latest(run_dir, scenario="coordinated")
    print(
        f"STATUS: {summary.status}\n"
        f"BATCHES: {summary.batches}\n"
        f"MERGED: {summary.merged_task_ids}\n"
        f"FINALIZER: {summary.final_decision}\n"
        f"ARTIFACT: {run_dir}"
    )


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
            "replace_text",
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


def run_evaluation() -> None:
    """发布真实 campaign 与已有 official case，供 Workbench 做离线复盘。"""

    from agent_forge.evaluation.api import (
        ImprovementRecordRequest,
        write_improvement_record,
    )

    if not (PUBLIC_CAMPAIGN_ROOT / "manifest.json").is_file():
        raise SystemExit(f"公开 benchmark campaign 不完整: {PUBLIC_CAMPAIGN_ROOT}")
    latest_dir = PROJECT_ROOT / ".agent_forge" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "campaign.txt").write_text(
        str(PUBLIC_CAMPAIGN_ROOT.resolve()),
        encoding="utf-8",
    )
    saved_official = STATE_ROOT / "state" / "astropy_artifact.txt"
    if saved_official.is_file():
        restore_evidence("astropy")

    summary = json.loads(
        (PUBLIC_CAMPAIGN_ROOT / "summary.json").read_text(encoding="utf-8")
    )
    minimal = summary["variants"]["minimal-control"]
    governed = summary["variants"]["governed-runtime"]
    improvement_path = write_improvement_record(
        ImprovementRecordRequest(
            campaign_dir=PUBLIC_CAMPAIGN_ROOT,
            observed_problem=(
                "Minimal-control 在两个 commissioning case 中出现 8 次失败工具调用，"
                "需要验证 task-aware routing 与 Skill 是否减少无效动作。"
            ),
            hypothesis=(
                "在相同 AgentLoop、模型、任务、预算和安全边界下，task-aware routing "
                "与 Skill 可减少失败工具调用，同时不降低 official resolved 结果。"
            ),
            change_ref="governed-runtime preset: task-aware routing + built-in Skills",
            decision="iterate",
            decision_rationale=(
                "失败工具调用由 8 降至 5，但 official outcome 为 2 个平局，"
                "token 与成本上升；保留方向并继续扩大样本，而不声称 correctness 提升。"
            ),
            claim_boundary=(
                "这是两个 case、单次重复的 post-hoc commissioning evidence，"
                "只证明证据闭环和已观察 trade-off，不是总体成功率或因果结论。"
            ),
        )
    )
    print(
        "LAB 3/3: real campaign -> layered correctness -> diagnosis "
        "-> paired runtime evidence"
    )
    print(
        "OFFICIAL: "
        f"minimal={minimal['official_resolved']}/{minimal['official_evaluated']} "
        f"governed={governed['official_resolved']}/{governed['official_evaluated']}"
    )
    print(
        "TRADE-OFF: "
        f"failed_tools {minimal['failed_tool_calls']} -> {governed['failed_tool_calls']}; "
        f"tokens {minimal['total_tokens']} -> {governed['total_tokens']}; "
        f"cost ${minimal['estimated_cost_usd']:.6f} -> "
        f"${governed['estimated_cost_usd']:.6f}"
    )
    print("CLAIM: no observed correctness delta; commissioning evidence only")
    print(f"IMPROVEMENT RECORD: {improvement_path}")
    print(f"ARTIFACT: {PUBLIC_CAMPAIGN_ROOT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        choices=(
            "governed",
            "coordinated",
            "evaluation",
            "single-live",
            "official-rerun",
            "show-live",
            "show-official",
        ),
    )
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)
    {
        "governed": run_governed,
        "coordinated": run_coordinated,
        "evaluation": run_evaluation,
        "single-live": run_live,
        "official-rerun": run_astropy,
        "show-live": lambda: restore_evidence("live"),
        "show-official": lambda: restore_evidence("astropy"),
    }[args.scenario]()


if __name__ == "__main__":
    main()
