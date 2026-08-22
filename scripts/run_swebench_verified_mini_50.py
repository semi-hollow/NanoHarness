#!/usr/bin/env python3
"""运行固定 SWE-bench Verified Mini-50，并复用 campaign 证据链。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.bench.adapters.campaign_files import (
    FileCampaignArtifacts,
    GitSourceIdentity,
)
from agent_forge.bench.api import run_benchmark_campaign
from agent_forge.bench.domain.campaign import (
    BenchmarkCampaignRequest,
    CampaignState,
    CampaignVariant,
    OFFICIAL_DECIDED,
    RETRYABLE_INFRASTRUCTURE_FAILURES,
    campaign_config_digest,
)
from agent_forge.bench.domain.cohort import load_benchmark_cohort
from agent_forge.bench.domain.config import SwebenchRunRequest, safe_id


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COHORT_PATH = PROJECT_ROOT / "benchmarks/showcase/swebench-verified-mini-50-v1.json"
DEFAULT_OUTPUT_ROOT = ".agent_forge/runs/benchmarks/swebench-verified-mini-50"
DEFAULT_PUBLISH_ROOT = "benchmarks/results/swebench-verified-mini-50"
OPEN_CODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_CONTEXT_CHARS = 64_000
DEFAULT_MAX_PROMPT_TOKENS = 131_072
DEFAULT_RESERVED_OUTPUT_TOKENS = 16_384
DEFAULT_SWEBENCH_HARNESS_ROOT = ".agent_forge/internal/debug-lab/tools/SWE-bench"
DEFAULT_OFFICIAL_PLATFORM = "linux/amd64"
DEFAULT_CASE_WORKERS = 2
SMOKE_GATE_CASE_IDS = (
    "django__django-11451",
    "sphinx-doc__sphinx-10323",
)
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
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        choices=["high", "max"],
        default="max",
    )
    parser.add_argument("--max-steps", type=_positive_int, default=128)
    parser.add_argument(
        "--max-context-chars",
        type=_positive_int,
        default=DEFAULT_MAX_CONTEXT_CHARS,
    )
    parser.add_argument(
        "--max-prompt-tokens",
        type=_positive_int,
        default=DEFAULT_MAX_PROMPT_TOKENS,
    )
    parser.add_argument(
        "--reserved-output-tokens",
        type=_positive_int,
        default=DEFAULT_RESERVED_OUTPUT_TOKENS,
    )
    parser.add_argument("--max-tool-calls-per-turn", type=_positive_int, default=4)
    parser.add_argument(
        "--case-workers",
        type=int,
        choices=[1, 2, 3],
        default=DEFAULT_CASE_WORKERS,
        help="Concurrent independent Pass@1 case slots; 2 is the quality-safe default.",
    )
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
        "--swebench-harness-root",
        default=DEFAULT_SWEBENCH_HARNESS_ROOT,
        help="Local SWE-bench source root used by the official evaluator.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Resume pending cases under the same campaign ID. A case that already "
            "started is never launched again under strict Pass@1."
        ),
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
        official_platform=DEFAULT_OFFICIAL_PLATFORM,
        agent_mode="single",
        profile="coding_fix",
        max_revision_rounds=0,
        tool_routing_mode="task-aware",
        skill_mode="auto",
        skill_names=("swebench_repair",),
        memory_root="",
        memory_namespace="",
        memory_max_chars=0,
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
        # Public publication happens only after the Mini-50-specific final gate.
        publish_root="",
        resume=args.resume,
        rerun_incomplete_slots=False,
        allow_dirty=args.allow_dirty,
        max_infrastructure_attempts=args.max_infrastructure_attempts,
        max_parallel_slots=args.case_workers,
        variants=(MINI_50_RUNTIME,),
        cohort=cohort,
    )


def build_frozen_plan(
    request: BenchmarkCampaignRequest,
    *,
    project_root: Path = PROJECT_ROOT,
    swebench_harness_root: str | Path = DEFAULT_SWEBENCH_HARNESS_ROOT,
) -> dict[str, Any]:
    """构造 outcome 前冻结、执行前后均可机械重算的安全身份。"""

    source_identity = GitSourceIdentity(project_root).read()
    harness_root = _resolve_harness_root(swebench_harness_root, project_root)
    harness_entrypoint = harness_root / "swebench/harness/run_evaluation.py"
    if not harness_entrypoint.is_file():
        raise RuntimeError(
            f"SWE-bench official harness is missing: {harness_entrypoint}"
        )
    campaign_identity = request.identity()
    benchmark = request.benchmark
    return {
        "schema_version": 1,
        "artifact_type": "swebench_verified_mini_50_frozen_plan",
        "campaign_id": request.campaign_id,
        "frozen_before_mini_50_outcomes": True,
        "source_identity": source_identity,
        "campaign_identity": campaign_identity,
        "campaign_identity_sha256": _json_sha256(campaign_identity),
        "cohort": {
            "manifest": str(COHORT_PATH.relative_to(PROJECT_ROOT)),
            "manifest_sha256": _sha256_file(COHORT_PATH),
            "ordered_case_ids_sha256": hashlib.sha256(
                "\n".join(request.case_ids).encode("utf-8")
            ).hexdigest(),
            "planned": len(request.case_ids),
            "dataset_name": benchmark.dataset_name,
            "dataset_revision": benchmark.dataset_revision,
            "split": benchmark.split,
        },
        "model_identity": {
            "provider": benchmark.provider,
            "requested_model": benchmark.model,
            "base_url": benchmark.base_url,
            "thinking_mode": benchmark.thinking_mode,
            "reasoning_effort": benchmark.reasoning_effort,
            "max_steps": benchmark.max_steps,
            "max_context_chars": benchmark.max_context_chars,
            "max_prompt_tokens": benchmark.max_prompt_tokens,
            "reserved_output_tokens": benchmark.reserved_output_tokens,
            "max_tool_calls_per_turn": benchmark.max_tool_calls_per_turn,
            "cost_budget_usd": benchmark.cost_budget_usd,
        },
        "official_evaluator": {
            "harness_root": str(swebench_harness_root),
            "entrypoint_sha256": _sha256_file(harness_entrypoint),
            "namespace": benchmark.official_namespace,
            "cache_level": benchmark.official_cache_level,
            "platform": benchmark.official_platform,
        },
        "smoke_gate": {
            "case_ids": list(SMOKE_GATE_CASE_IDS),
            "correctness_is_not_a_gate": True,
        },
        "pass_at_one": {
            "started_slot_policy": "never_restart; fail_closed_if_not_terminal",
            "whole_case_attempts": request.max_infrastructure_attempts,
            "provider_request_attempts": benchmark.model_request_max_attempts,
            "empty_patch_counts_in_planned_denominator": True,
        },
        "final_publish_gate": {
            "planned": 50,
            "terminal_accounted": 50,
            "provider_infra": 0,
            "runtime_infra": 0,
            "evaluator_infra": 0,
            "identity_drift": 0,
        },
        "claim": "fixed HAL SWE-bench Verified Mini-50 Pass@1 snapshot",
        "not_claimed": "full 500-case SWE-bench Verified leaderboard score",
    }


def render_plan(
    request: BenchmarkCampaignRequest,
    *,
    execute: bool,
    frozen_plan: dict[str, Any] | None = None,
) -> str:
    """只输出无密钥的运行计划，供启动前核对或自动归档。"""

    benchmark = request.benchmark
    plan = frozen_plan or build_frozen_plan(request)
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
        "max_context_chars": benchmark.max_context_chars,
        "max_prompt_tokens": benchmark.max_prompt_tokens,
        "reserved_output_tokens": benchmark.reserved_output_tokens,
        "max_tool_calls_per_turn": benchmark.max_tool_calls_per_turn,
        "cost_budget_usd": benchmark.cost_budget_usd,
        "case_timeout_seconds": int(benchmark.timeout_seconds),
        "model_request_timeout_seconds": benchmark.model_request_timeout_seconds,
        "tool_execution_timeout_seconds": benchmark.tool_execution_timeout_seconds,
        "model_request_max_attempts": benchmark.model_request_max_attempts,
        "whole_case_attempts": request.max_infrastructure_attempts,
        "case_workers": request.max_parallel_slots,
        "official_evaluator": benchmark.evaluate,
        "output_root": request.output_root,
        "resume": request.resume,
        "source_clean_required": not request.allow_dirty,
        "started_slot_policy": plan["pass_at_one"]["started_slot_policy"],
        "frozen_plan_sha256": _json_sha256(plan),
        "source_revision": plan["source_identity"]["revision"],
        "cohort_manifest_sha256": plan["cohort"]["manifest_sha256"],
        "ordered_case_ids_sha256": plan["cohort"]["ordered_case_ids_sha256"],
        "dataset_revision": plan["cohort"]["dataset_revision"],
        "official_harness_entrypoint_sha256": plan["official_evaluator"][
            "entrypoint_sha256"
        ],
        "claim": "fixed HAL SWE-bench Verified Mini-50 Pass@1 snapshot",
        "not_claimed": "full 500-case SWE-bench Verified leaderboard score",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    """校验或执行评测；只有 ``--execute`` 分支会进入模型调用。"""

    args = build_parser().parse_args(argv)
    request = build_campaign_request(args)
    if args.execute and not os.getenv("OPENCODE_GO_API_KEY", "").strip():
        raise RuntimeError(
            "OPENCODE_GO_API_KEY is missing; refusing to fall back to another account"
        )
    frozen_plan = build_frozen_plan(
        request,
        swebench_harness_root=args.swebench_harness_root,
    )
    frozen_plan_path = _freeze_or_validate_plan(request, frozen_plan)
    print(render_plan(request, execute=args.execute, frozen_plan=frozen_plan))
    print(f"frozen_plan={frozen_plan_path}")
    if not args.execute:
        print("VALIDATED_ONLY: frozen plan persisted; no provider request was sent.")
        return 0
    _configure_swebench_harness(Path(args.swebench_harness_root))
    if (
        build_frozen_plan(
            request,
            swebench_harness_root=args.swebench_harness_root,
        )
        != frozen_plan
    ):
        raise RuntimeError("source/config/cohort/model drift before first model call")
    result = run_benchmark_campaign(request, project_dir=PROJECT_ROOT)
    final_plan = build_frozen_plan(
        request,
        swebench_harness_root=args.swebench_harness_root,
    )
    gate = build_final_publish_gate(
        result.state,
        request=request,
        frozen_plan=frozen_plan,
        final_plan=final_plan,
    )
    gate_path = result.campaign_dir / "final_publish_gate.json"
    atomic_write_json(gate_path, gate)
    print(f"campaign_dir={result.campaign_dir}")
    print(f"summary={result.summary_path}")
    print(f"report={result.report_path}")
    print(f"final_publish_gate={gate_path}")
    print(f"publishable={str(gate['publishable']).lower()}")
    if args.publish and gate["publishable"]:
        summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
        published = FileCampaignArtifacts(PROJECT_ROOT).publish_public_bundle(
            args.publish_root,
            result.campaign_dir,
            result.state,
            summary,
        )
        print(f"published_bundle={published}")
    elif args.publish:
        print("PUBLICATION_REFUSED: final Mini-50 gate did not pass.")
    return 0 if gate["publishable"] else 2


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
        "case_workers": args.case_workers,
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


def _configure_swebench_harness(root: Path) -> None:
    """在首个付费调用前绑定本地 official evaluator 源码。"""

    resolved = _resolve_harness_root(root, PROJECT_ROOT)
    entrypoint = resolved / "swebench/harness/run_evaluation.py"
    if not entrypoint.is_file():
        raise RuntimeError(f"SWE-bench official harness is missing: {entrypoint}")
    text = str(resolved)
    if text not in sys.path:
        sys.path.insert(0, text)
    existing = os.environ.get("PYTHONPATH", "")
    items = [item for item in existing.split(os.pathsep) if item]
    if text not in items:
        os.environ["PYTHONPATH"] = os.pathsep.join([text, *items])


def _resolve_harness_root(root: str | Path, project_root: Path) -> Path:
    path = Path(root).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _freeze_or_validate_plan(
    request: BenchmarkCampaignRequest,
    payload: dict[str, Any],
    *,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """完整文件一次性落盘；同 campaign 只接受 byte-exact 重算。"""

    output_root = Path(request.output_root)
    if not output_root.is_absolute():
        output_root = project_root / output_root
    destination = output_root / request.campaign_id / "frozen_plan.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = _json_bytes(payload)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or destination.read_bytes() != raw:
            raise RuntimeError(
                "frozen Mini-50 plan drift; use a new campaign ID before outcomes"
            )
        return destination

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".frozen_plan.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or destination.read_bytes() != raw:
                raise RuntimeError("concurrent frozen Mini-50 plan drift") from None
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def build_final_publish_gate(
    state: CampaignState,
    *,
    request: BenchmarkCampaignRequest,
    frozen_plan: dict[str, Any],
    final_plan: dict[str, Any],
) -> dict[str, Any]:
    """只在完整分母、零基础设施故障和零身份漂移时放行 X/50。"""

    terminal_statuses = {"completed", "failed"}
    terminal_accounted = sum(
        record.status in terminal_statuses for record in state.records
    )
    empty_patch = sum(
        record.status == "completed"
        and not bool(record.evidence.get("patch_generated"))
        for record in state.records
    )
    official_evaluated = sum(
        record.status == "completed"
        and str(record.evidence.get("official_evaluation_status") or "")
        in OFFICIAL_DECIDED
        for record in state.records
    )
    official_resolved = sum(
        record.status == "completed"
        and record.evidence.get("official_evaluation_status") == "official_resolved"
        for record in state.records
    )
    provider_infra = sum(
        record.evidence.get("failure_class") == "provider_transport_error"
        for record in state.records
    )
    runtime_infra = sum(
        record.status == "failed"
        or record.evidence.get("failure_class") == "runner_or_environment_error"
        for record in state.records
    )
    evaluator_infra = sum(
        record.evidence.get("failure_class") == "official_eval_error"
        or (
            record.status == "completed"
            and bool(record.evidence.get("patch_generated"))
            and str(record.evidence.get("official_evaluation_status") or "")
            not in OFFICIAL_DECIDED
        )
        for record in state.records
    )
    identity_checks = {
        "final_plan_matches_frozen": final_plan == frozen_plan,
        "state_config_matches_frozen": state.config == request.identity(),
        "state_source_matches_frozen": state.source == frozen_plan["source_identity"],
        "state_config_digest_matches": state.config_digest
        == campaign_config_digest(request.identity(), state.source),
        "source_is_clean": not bool(final_plan["source_identity"].get("dirty")),
    }
    planned = len(state.records)
    publishable = (
        planned == 50
        and terminal_accounted == 50
        and official_evaluated + empty_patch == 50
        and provider_infra == 0
        and runtime_infra == 0
        and evaluator_infra == 0
        and all(identity_checks.values())
        and state.status == "completed"
    )
    return {
        "schema_version": 1,
        "artifact_type": "swebench_verified_mini_50_final_publish_gate",
        "campaign_id": state.campaign_id,
        "publishable": publishable,
        "headline": f"{official_resolved}/50" if publishable else None,
        "planned": planned,
        "terminal_accounted": terminal_accounted,
        "official_evaluated": official_evaluated,
        "empty_patch": empty_patch,
        "official_resolved": official_resolved,
        "provider_infra": provider_infra,
        "runtime_infra": runtime_infra,
        "evaluator_infra": evaluator_infra,
        "identity_checks": identity_checks,
        "frozen_plan_sha256": _json_sha256(frozen_plan),
        "failure_classes_considered_infrastructure": sorted(
            RETRYABLE_INFRASTRUCTURE_FAILURES
        ),
    }


def _json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _positive_int(value: str) -> int:
    """解析严格正整数，避免付费运行后才发现预算参数无效。"""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
