from __future__ import annotations

import argparse
import os

from agent_forge.bench.api import run_swebench
from agent_forge.bench.domain.catalog import (
    DEFAULT_DATASET,
    REGRESSION_SETS,
    SHOWCASE_INSTANCE_ID,
    SHOWCASE_INSTANCE_NOTE,
)
from agent_forge.bench.domain.models import BenchRunSummary


def build_swebench_parser(parser: argparse.ArgumentParser) -> None:
    """Attach SWE-bench options to the CLI subparser."""

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
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--max-context-chars", type=int, default=12000)
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


def run_swebench_from_args(args: argparse.Namespace) -> BenchRunSummary:
    """Translate CLI arguments into the stable benchmark API."""

    instance_ids = args.instance_id
    limit = args.limit
    if args.regression_set and not instance_ids:
        instance_ids = REGRESSION_SETS[args.regression_set]
        limit = len(instance_ids)
    elif args.showcase and not instance_ids:
        instance_ids = [SHOWCASE_INSTANCE_ID]
        limit = 1

    return run_swebench(
        dataset_name=args.dataset,
        split=args.split,
        limit=limit,
        instance_ids=instance_ids,
        cases_file=args.cases_file,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
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
        execution_mode=args.execution_mode,
        network_policy=args.network_policy,
        keep_worktree=args.keep_worktree,
        container_runtime=args.container_runtime,
        container_image=args.container_image,
        container_cpus=args.container_cpus,
        container_memory=args.container_memory,
        container_pids_limit=args.container_pids_limit,
        container_read_only=args.container_read_only,
    )
