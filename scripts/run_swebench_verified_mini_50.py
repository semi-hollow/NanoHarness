#!/usr/bin/env python3
"""运行固定 SWE-bench Verified Mini-50，并复用 campaign 证据链。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

from agent_forge.bench.api import run_benchmark_campaign
from agent_forge.bench.domain.campaign import (
    BenchmarkCampaignRequest,
    CampaignVariant,
)
from agent_forge.bench.domain.cohort import load_benchmark_cohort
from agent_forge.bench.domain.config import SwebenchRunRequest, safe_id


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COHORT_PATH = PROJECT_ROOT / "benchmarks/showcase/swebench-verified-mini-50-v1.json"
DEFAULT_OUTPUT_ROOT = ".agent_forge/runs/benchmarks/swebench-verified-mini-50"
DEFAULT_PUBLISH_ROOT = "benchmarks/results/swebench-verified-mini-50"
OPEN_CODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
MINI_50_RUNTIME = CampaignVariant(
    name="canonical-runtime",
    label="NanoHarness Canonical Runtime",
    description=(
        "Single AgentLoop with task-aware tool routing and the pinned SWE-bench repair Skill."
    ),
    tool_routing_mode="task-aware",
    skill_mode="auto",
    skill_names=("swebench_repair",),
)


def build_parser() -> argparse.ArgumentParser:
    """定义一个默认只校验、显式执行才消耗模型额度的入口。"""

    parser = argparse.ArgumentParser(
        description=(
            "Run the published HAL SWE-bench Verified Mini-50 with NanoHarness. "
            "Without --execute this command only validates and prints the frozen plan."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Start the 50 paid model runs after local validation.",
    )
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument(
        "--reasoning-effort",
        choices=["high", "max"],
        default="max",
    )
    parser.add_argument("--max-steps", type=_positive_int, default=128)
    parser.add_argument("--max-context-chars", type=_positive_int, default=12_000)
    parser.add_argument("--max-prompt-tokens", type=_positive_int, default=49_152)
    parser.add_argument("--reserved-output-tokens", type=_positive_int, default=4_096)
    parser.add_argument("--max-tool-calls-per-turn", type=_positive_int, default=4)
    parser.add_argument("--case-timeout-seconds", type=_positive_int, default=3_600)
    parser.add_argument(
        "--model-request-timeout-seconds",
        type=_positive_int,
        default=600,
    )
    parser.add_argument(
        "--model-request-max-attempts",
        type=_positive_int,
        choices=[1, 2],
        default=2,
        help="At most one identical transport retry; this is not a correctness rerun.",
    )
    parser.add_argument(
        "--tool-execution-timeout-seconds",
        type=_positive_int,
        default=600,
    )
    parser.add_argument(
        "--max-infrastructure-attempts",
        type=int,
        choices=[1, 2],
        default=1,
        help="Whole-case attempts. The default disables whole-case reruns.",
    )
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--repo-cache",
        default=".agent_forge/internal/cache/bench/repos",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume the same campaign ID and skip completed cases.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow uncommitted source while recording its content digest.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Also write a sanitized bundle under --publish-root after completion.",
    )
    parser.add_argument("--publish-root", default=DEFAULT_PUBLISH_ROOT)
    return parser


def build_campaign_request(
    args: argparse.Namespace,
    *,
    project_root: Path = PROJECT_ROOT,
) -> BenchmarkCampaignRequest:
    """把 CLI 参数冻结成一个 50 题、单配置、Pass@1 campaign。"""

    cohort = load_benchmark_cohort(COHORT_PATH).select_shard("all")
    if len(cohort.case_ids) != 50:
        raise ValueError("Mini-50 manifest must contain exactly 50 cases")
    campaign_id = args.campaign_id or _default_campaign_id(args, project_root)
    benchmark = SwebenchRunRequest(
        dataset_name=cohort.dataset_name,
        dataset_revision=cohort.dataset_revision,
        split=cohort.split,
        limit=1,
        provider="opencode-go",
        model=args.model,
        base_url=OPEN_CODE_GO_BASE_URL,
        temperature=0.0,
        thinking_mode="enabled",
        reasoning_effort=args.reasoning_effort,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        max_prompt_tokens=args.max_prompt_tokens,
        reserved_output_tokens=args.reserved_output_tokens,
        max_tool_calls_per_turn=args.max_tool_calls_per_turn,
        cost_budget_usd=None,
        timeout_seconds=float(args.case_timeout_seconds),
        model_request_timeout_seconds=args.model_request_timeout_seconds,
        model_request_max_attempts=args.model_request_max_attempts,
        tool_execution_timeout_seconds=args.tool_execution_timeout_seconds,
        repo_cache=args.repo_cache,
        evaluate=True,
        max_workers=1,
        official_namespace="swebench",
        namespace_empty=False,
        official_cache_level="env",
        agent_mode="single",
        profile="coding_fix",
        max_revision_rounds=0,
        tool_routing_mode="task-aware",
        skill_mode="auto",
        skill_names=("swebench_repair",),
        memory_root="",
        memory_namespace="",
        memory_recall_limit=0,
        execution_mode="local",
        network_policy="deny",
        keep_worktree=False,
    )
    return BenchmarkCampaignRequest(
        benchmark=benchmark,
        case_ids=cohort.case_ids,
        campaign_id=campaign_id,
        regression_set=cohort.cohort_id,
        repetitions=1,
        output_root=args.output_root,
        publish_root=args.publish_root if args.publish else "",
        resume=args.resume,
        allow_dirty=args.allow_dirty,
        max_infrastructure_attempts=args.max_infrastructure_attempts,
        variants=(MINI_50_RUNTIME,),
        cohort=cohort,
    )


def render_plan(request: BenchmarkCampaignRequest, *, execute: bool) -> str:
    """只输出无密钥的运行计划，供启动前核对或自动归档。"""

    benchmark = request.benchmark
    payload: dict[str, Any] = {
        "mode": "execute" if execute else "validate_only",
        "paid_model_calls_started": False,
        "campaign_id": request.campaign_id,
        "cohort_id": request.regression_set,
        "case_count": len(request.case_ids),
        "pass_at": 1,
        "provider": benchmark.provider,
        "model": benchmark.model,
        "base_url": benchmark.base_url,
        "thinking_mode": benchmark.thinking_mode,
        "reasoning_effort": benchmark.reasoning_effort,
        "max_steps": benchmark.max_steps,
        "cost_budget_usd": benchmark.cost_budget_usd,
        "model_request_max_attempts": benchmark.model_request_max_attempts,
        "whole_case_attempts": request.max_infrastructure_attempts,
        "official_evaluator": benchmark.evaluate,
        "output_root": request.output_root,
        "resume": request.resume,
        "source_clean_required": not request.allow_dirty,
        "claim": "fixed HAL SWE-bench Verified Mini-50 Pass@1 snapshot",
        "not_claimed": "full 500-case SWE-bench Verified leaderboard score",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    """校验或执行评测；只有 ``--execute`` 分支会进入模型调用。"""

    args = build_parser().parse_args(argv)
    request = build_campaign_request(args)
    print(render_plan(request, execute=args.execute))
    if not args.execute:
        print("VALIDATED_ONLY: no provider request was sent.")
        return 0
    if not os.getenv("OPENCODE_GO_API_KEY", "").strip():
        raise RuntimeError(
            "OPENCODE_GO_API_KEY is missing; refusing to fall back to another account"
        )
    result = run_benchmark_campaign(request, project_dir=PROJECT_ROOT)
    print(f"campaign_dir={result.campaign_dir}")
    print(f"summary={result.summary_path}")
    print(f"report={result.report_path}")
    if result.published_bundle_dir is not None:
        print(f"published_bundle={result.published_bundle_dir}")
    return 0 if result.state.status == "completed" else 2


def _default_campaign_id(args: argparse.Namespace, project_root: Path) -> str:
    """按源码与质量配置生成稳定 ID；同配置重跑即恢复，不会创建重复实验。"""

    revision = _git_revision(project_root)[:10]
    profile = {
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_steps": args.max_steps,
        "max_context_chars": args.max_context_chars,
        "max_prompt_tokens": args.max_prompt_tokens,
        "reserved_output_tokens": args.reserved_output_tokens,
        "max_tool_calls_per_turn": args.max_tool_calls_per_turn,
        "case_timeout_seconds": args.case_timeout_seconds,
        "model_request_timeout_seconds": args.model_request_timeout_seconds,
        "model_request_max_attempts": args.model_request_max_attempts,
        "tool_execution_timeout_seconds": args.tool_execution_timeout_seconds,
        "max_infrastructure_attempts": args.max_infrastructure_attempts,
    }
    encoded = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    profile_digest = hashlib.sha256(encoded).hexdigest()[:10]
    return safe_id(f"mini50-v1-{args.model}-{revision}-{profile_digest}")[:80]


def _git_revision(project_root: Path) -> str:
    """读取当前提交作为默认 campaign 身份的一部分。"""

    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        text=True,
        capture_output=True,
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise RuntimeError("cannot resolve the NanoHarness source revision")
    return process.stdout.strip()


def _positive_int(value: str) -> int:
    """解析严格正整数，避免付费运行后才发现预算参数无效。"""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
