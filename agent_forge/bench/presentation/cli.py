"""Benchmark CLI 的参数与输出适配层。

阅读入口：``run_swebench_from_args`` 进入执行链；两个 ``render_*_from_args``
进入只读 Case Explorer。``build_*_parser`` 只注册参数，``publish_case_document``
只负责终端或文件输出，均不拥有 benchmark 规则。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent_forge.bench.api import (
    create_campaign_id,
    get_regression_set_profile,
    inspect_swebench_case,
    list_regression_case_profiles,
    run_benchmark_campaign,
    run_swebench,
)
from agent_forge.bench.application.campaign import BenchmarkCampaignResult
from agent_forge.bench.domain.catalog import (
    DEFAULT_DATASET,
    REGRESSION_SETS,
    SHOWCASE_INSTANCE_ID,
    SHOWCASE_INSTANCE_NOTE,
)
from agent_forge.bench.domain.campaign import BenchmarkCampaignRequest
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary
from agent_forge.bench.presentation.case_inspection import (
    render_case_catalog,
    render_case_inspection,
)


def build_case_catalog_parser(parser: argparse.ArgumentParser) -> None:
    """注册固定回归集合的可解释目录命令。"""

    parser.add_argument(
        "--regression-set",
        choices=sorted(REGRESSION_SETS),
        default="smoke-5",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="同时把 Markdown 或 JSON 写入指定文件。")


def build_case_inspection_parser(parser: argparse.ArgumentParser) -> None:
    """注册单个 case 的输入、测试契约和受控复盘命令。"""

    parser.add_argument("instance_id")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default="test")
    parser.add_argument("--cases-file")
    parser.add_argument("--show-test-patch", action="store_true")
    parser.add_argument("--show-gold", action="store_true")
    parser.add_argument("--all-tests", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="同时把 Markdown 或 JSON 写入指定文件。")


def build_swebench_parser(parser: argparse.ArgumentParser) -> None:
    """注册 benchmark 执行、实验身份和隔离环境参数。"""

    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument(
        "--showcase",
        action="store_true",
        help=(
            f"Run the fixed reference case {SHOWCASE_INSTANCE_ID} for repeatable "
            f"before/after comparisons. {SHOWCASE_INSTANCE_NOTE}"
        ),
    )
    parser.add_argument(
        "--regression-set",
        choices=sorted(REGRESSION_SETS),
        help="Run a named fixed SWE-bench case set for harness regression checks.",
    )
    parser.add_argument("--cases-file")
    parser.add_argument(
        "--provider",
        default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"),
    )
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature recorded in run identity (0.0-2.0).",
    )
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--max-context-chars", type=int, default=12000)
    parser.add_argument("--max-prompt-tokens", type=int, default=32_768)
    parser.add_argument("--reserved-output-tokens", type=int, default=4_096)
    parser.add_argument("--max-tool-calls-per-turn", type=int, default=4)
    parser.add_argument("--cost-budget-usd", type=float)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--repo-cache", default=".agent_forge/bench/repos")
    parser.add_argument("--output-root", default=".agent_forge/runs")
    parser.add_argument("--direct-baseline", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--namespace-empty", action="store_true")
    parser.add_argument(
        "--agent-mode",
        default="single",
        choices=["single", "multi", "compare"],
    )
    parser.add_argument("--profile", default="coding_fix", choices=["coding_fix"])
    parser.add_argument("--max-revision-rounds", type=int, default=2)
    parser.add_argument(
        "--tool-routing",
        choices=["task-aware", "all"],
        default="task-aware",
        help=(
            "Select task-aware tool visibility or expose all tools for a controlled "
            "ablation; runtime safety policy remains enabled."
        ),
    )
    parser.add_argument(
        "--skills",
        default="auto",
        help="auto, none, or comma-separated skill names for matched ablation.",
    )
    parser.add_argument("--skill-manifest", action="append", default=[])
    parser.add_argument(
        "--memory-root",
        default="",
        help="Frozen evidence-backed memory store; empty keeps benchmark recall disabled.",
    )
    parser.add_argument(
        "--memory-namespace",
        default="",
        help="Stable namespace, or swebench:<instance_id> when omitted.",
    )
    parser.add_argument(
        "--memory-recall-limit",
        type=int,
        default=0,
        help="Enable matched memory ablation explicitly; default 0 prevents leakage.",
    )
    parser.add_argument(
        "--execution-mode",
        choices=["local", "worktree", "container"],
        default="local",
        help=(
            "Run each case locally, in an extra detached worktree, or in a "
            "constrained OCI container."
        ),
    )
    parser.add_argument("--network-policy", choices=["deny", "allow"], default="deny")
    parser.add_argument(
        "--keep-worktree",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Retain the extra per-case execution snapshot; benchmark base "
            "workspaces remain in the run directory."
        ),
    )
    parser.add_argument("--container-runtime", default="docker")
    parser.add_argument("--container-image", default="python:3.11-slim")
    parser.add_argument("--container-cpus", type=float, default=1.0)
    parser.add_argument("--container-memory", default="1g")
    parser.add_argument("--container-pids-limit", type=int, default=256)
    parser.add_argument(
        "--container-read-only",
        action=argparse.BooleanOptionalAction,
        default=True,
    )


def build_campaign_parser(parser: argparse.ArgumentParser) -> None:
    """注册可恢复的 Smoke-5 repeated matched campaign 参数。"""

    parser.add_argument("--campaign-id")
    parser.add_argument(
        "--regression-set",
        choices=sorted(REGRESSION_SETS),
        default="smoke-5",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default="test")
    parser.add_argument(
        "--provider",
        default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"),
    )
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--max-context-chars", type=int, default=12000)
    parser.add_argument("--max-prompt-tokens", type=int, default=32_768)
    parser.add_argument("--reserved-output-tokens", type=int, default=4_096)
    parser.add_argument("--max-tool-calls-per-turn", type=int, default=4)
    parser.add_argument("--cost-budget-usd", type=float)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--repo-cache", default=".agent_forge/bench/repos")
    parser.add_argument("--output-root", default=".agent_forge/campaigns")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--namespace-empty", action="store_true")
    parser.add_argument(
        "--execution-mode",
        choices=["local", "worktree", "container"],
        default="worktree",
    )
    parser.add_argument("--network-policy", choices=["deny", "allow"], default="deny")
    parser.add_argument(
        "--keep-worktree",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume the same campaign id and retry incomplete slots.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow an uncommitted source snapshot and record its content digest.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write a sanitized, reviewable evidence bundle under --publish-root.",
    )
    parser.add_argument("--publish-root", default="benchmarks/campaigns")


# 主要入口：把扁平 CLI 参数收敛成 SwebenchRunRequest 并启动正式评测用例。
def run_swebench_from_args(args: argparse.Namespace) -> BenchRunSummary:
    """处理固定集合/showcase 语义后调用 ``bench.api.run_swebench``。"""

    instance_ids = args.instance_id
    limit = args.limit
    if args.regression_set and not instance_ids:
        instance_ids = REGRESSION_SETS[args.regression_set]
        limit = len(instance_ids)
    elif args.showcase and not instance_ids:
        instance_ids = [SHOWCASE_INSTANCE_ID]
        limit = 1

    skill_value = str(args.skills or "auto")
    skill_names = (
        ()
        if skill_value in {"auto", "none"}
        else tuple(item.strip() for item in skill_value.split(",") if item.strip())
    )
    return run_swebench(SwebenchRunRequest(
        dataset_name=args.dataset,
        split=args.split,
        limit=limit,
        instance_ids=tuple(instance_ids),
        cases_file=args.cases_file,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        max_prompt_tokens=args.max_prompt_tokens,
        reserved_output_tokens=args.reserved_output_tokens,
        max_tool_calls_per_turn=args.max_tool_calls_per_turn,
        cost_budget_usd=args.cost_budget_usd,
        timeout_seconds=args.timeout_seconds,
        repo_cache=args.repo_cache,
        output_root=args.output_root,
        direct_baseline=args.direct_baseline,
        evaluate=args.evaluate,
        max_workers=args.max_workers,
        namespace_empty=args.namespace_empty,
        agent_mode=args.agent_mode,
        profile=args.profile,
        max_revision_rounds=args.max_revision_rounds,
        tool_routing_mode=args.tool_routing,
        skill_mode="none" if skill_value == "none" else "auto",
        skill_names=skill_names,
        skill_manifest_files=tuple(args.skill_manifest),
        memory_root=args.memory_root,
        memory_namespace=args.memory_namespace,
        memory_recall_limit=args.memory_recall_limit,
        execution_mode=args.execution_mode,
        network_policy=args.network_policy,
        keep_worktree=args.keep_worktree,
        container_runtime=args.container_runtime,
        container_image=args.container_image,
        container_cpus=args.container_cpus,
        container_memory=args.container_memory,
        container_pids_limit=args.container_pids_limit,
        container_read_only=args.container_read_only,
    ))


# 主要入口：把 CLI 参数固定为两个可解释 Runtime preset，并启动 campaign。
def run_campaign_from_args(args: argparse.Namespace) -> BenchmarkCampaignResult:
    """Campaign 不接受任意 factor 组合，避免 UI/CLI 产生不可解释实验。"""

    case_ids = tuple(REGRESSION_SETS[args.regression_set])
    campaign_id = args.campaign_id or create_campaign_id(args.regression_set)
    benchmark = SwebenchRunRequest(
        dataset_name=args.dataset,
        split=args.split,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        max_prompt_tokens=args.max_prompt_tokens,
        reserved_output_tokens=args.reserved_output_tokens,
        max_tool_calls_per_turn=args.max_tool_calls_per_turn,
        cost_budget_usd=args.cost_budget_usd,
        timeout_seconds=args.timeout_seconds,
        repo_cache=args.repo_cache,
        evaluate=args.evaluate,
        max_workers=args.max_workers,
        namespace_empty=args.namespace_empty,
        execution_mode=args.execution_mode,
        network_policy=args.network_policy,
        keep_worktree=args.keep_worktree,
    )
    return run_benchmark_campaign(
        BenchmarkCampaignRequest(
            benchmark=benchmark,
            case_ids=case_ids,
            campaign_id=campaign_id,
            regression_set=args.regression_set,
            repetitions=args.repetitions,
            output_root=args.output_root,
            publish_root=args.publish_root if args.publish else "",
            resume=args.resume,
            allow_dirty=args.allow_dirty,
        )
    )


# 主要入口：查询固定集合契约并生成 Markdown 或 JSON 文本。
def render_case_catalog_from_args(args: argparse.Namespace) -> str:
    """把集合选择契约渲染成 Markdown 或 JSON。"""

    set_profile = get_regression_set_profile(args.regression_set)
    profiles = list_regression_case_profiles(args.regression_set)
    if args.json:
        return json.dumps(
            {
                "set": set_profile.to_dict(),
                "cases": [profile.to_dict() for profile in profiles],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    return render_case_catalog(set_profile, profiles)


# 主要入口：读取单题并按显式防泄漏开关生成 Markdown 或 JSON。
def render_case_inspection_from_args(args: argparse.Namespace) -> str:
    """读取单个 case，并按显式泄漏开关渲染。"""

    inspection = inspect_swebench_case(
        args.instance_id,
        dataset_name=args.dataset,
        split=args.split,
        cases_file=args.cases_file,
    )
    if args.json:
        return json.dumps(
            inspection.to_dict(
                include_test_patch=args.show_test_patch,
                include_gold_patch=args.show_gold,
            ),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    return render_case_inspection(
        inspection,
        show_test_patch=args.show_test_patch,
        show_gold_patch=args.show_gold,
        show_all_tests=args.all_tests,
    )


def publish_case_document(content: str, output: str | None) -> None:
    """输出适配器：打印 case 文档，或将同一内容写入指定路径。"""

    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"Case document: {path}")
        return
    print(content, end="")
