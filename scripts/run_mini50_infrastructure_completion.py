#!/usr/bin/env python3
"""只补跑 Mini-50 中由外部基础设施导致的十个无效槽位。"""

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
from agent_forge.bench.api import run_benchmark_campaign
from agent_forge.bench.domain.campaign import (
    BenchmarkCampaignRequest,
    CampaignState,
    CampaignVariant,
)
from agent_forge.bench.domain.config import SwebenchRunRequest


SOURCE_REVISION = "3ec537113a26491b7b7a51e323a3d3af40f4754f"
PARENT_CAMPAIGN_ID = "mini50-v1-deepseek-v4-flash-3ec537113a-9f81b94c99"
REPAIR_CAMPAIGN_ID = "mini50-v1.1-infra-completion-deepseek-v4-flash-3ec537113a"
DATASET_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
BASE_URL = "https://opencode.ai/zen/go/v1"
EXPECTED_IMAGE_PLATFORM = "linux/amd64"
REPAIR_SELECTION: tuple[tuple[int, str, str], ...] = (
    (5, "django__django-13346", "external_manual_interruption"),
    (6, "django__django-13809", "external_manual_interruption"),
    (40, "django__django-16950", "provider_transport_error"),
    (41, "sympy__sympy-15349", "provider_transport_error"),
    (44, "sympy__sympy-12481", "provider_transport_error"),
    (45, "sympy__sympy-15875", "provider_transport_error"),
    (46, "sphinx-doc__sphinx-9461", "provider_transport_error"),
    (47, "sympy__sympy-20428", "provider_transport_error"),
    (49, "scikit-learn__scikit-learn-25747", "provider_transport_error"),
    (50, "scikit-learn__scikit-learn-25973", "provider_transport_error"),
)
RUNTIME = CampaignVariant(
    name="canonical-runtime",
    label="NanoHarness Canonical Runtime",
    description=(
        "Single AgentLoop with task-aware tool routing and the pinned "
        "SWE-bench repair Skill."
    ),
    tool_routing_mode="task-aware",
    skill_mode="auto",
    skill_names=("swebench_repair",),
)
INFRA_FAILURES = {
    "provider_transport_error",
    "runner_or_environment_error",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or execute the frozen Mini-50 infrastructure completion."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--source-worktree", required=True)
    parser.add_argument("--repo-cache", required=True)
    parser.add_argument("--swebench-harness-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--case-workers", type=int, choices=[1, 2], default=2)
    return parser


def build_request(args: argparse.Namespace) -> BenchmarkCampaignRequest:
    benchmark = SwebenchRunRequest(
        dataset_name="princeton-nlp/SWE-bench_Verified",
        dataset_revision=DATASET_REVISION,
        split="test",
        limit=1,
        provider="opencode-go",
        model="deepseek-v4-flash",
        base_url=BASE_URL,
        temperature=0.0,
        thinking_mode="enabled",
        reasoning_effort="max",
        max_steps=128,
        max_context_chars=64_000,
        max_prompt_tokens=131_072,
        reserved_output_tokens=16_384,
        max_tool_calls_per_turn=4,
        cost_budget_usd=None,
        timeout_seconds=3_600.0,
        model_request_timeout_seconds=600,
        model_request_max_attempts=2,
        tool_execution_timeout_seconds=600,
        repo_cache=args.repo_cache,
        evaluate=True,
        max_workers=1,
        official_namespace="swebench",
        namespace_empty=False,
        official_cache_level="env",
        official_platform=EXPECTED_IMAGE_PLATFORM,
        agent_mode="single",
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
        case_ids=tuple(case_id for _, case_id, _ in REPAIR_SELECTION),
        campaign_id=REPAIR_CAMPAIGN_ID,
        regression_set="mini50-v1.1-infrastructure-completion",
        repetitions=1,
        output_root=args.output_root,
        publish_root="",
        resume=True,
        rerun_incomplete_slots=False,
        allow_dirty=False,
        max_infrastructure_attempts=1,
        max_parallel_slots=args.case_workers,
        variants=(RUNTIME,),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root).resolve()
    source_worktree = Path(args.source_worktree).resolve()
    output_root = Path(args.output_root).resolve()
    parent_dir = (
        project_root
        / ".agent_forge/runs/benchmarks/swebench-verified-mini-50"
        / PARENT_CAMPAIGN_ID
    )
    _validate_source(source_worktree)
    _validate_parent_selection(parent_dir)
    _configure_environment(args)
    request = build_request(args)
    plan = _build_plan(
        request=request,
        project_root=project_root,
        source_worktree=source_worktree,
        parent_dir=parent_dir,
    )
    campaign_dir = output_root / REPAIR_CAMPAIGN_ID
    plan_path = _freeze_once(campaign_dir / "infrastructure_completion_plan.json", plan)
    print(
        json.dumps(_safe_plan_projection(plan, args.execute), indent=2, sort_keys=True)
    )
    print(f"frozen_plan={plan_path}")
    if not args.execute:
        print("VALIDATED_ONLY: no provider request was sent.")
        return 0
    if not os.getenv("OPENCODE_GO_API_KEY", "").strip():
        raise RuntimeError(
            "OPENCODE_GO_API_KEY is required; provider mixing is forbidden"
        )
    if (
        _build_plan(
            request=request,
            project_root=project_root,
            source_worktree=source_worktree,
            parent_dir=parent_dir,
        )
        != plan
    ):
        raise RuntimeError("repair source/config/selection/image identity drift")

    result = run_benchmark_campaign(request, project_dir=source_worktree)
    combined = _combine_results(parent_dir, result.state, plan)
    atomic_write_json(campaign_dir / "combined_result.json", combined)
    _write_text_atomic(campaign_dir / "combined_report.md", _render_report(combined))
    print(f"campaign_dir={campaign_dir}")
    print(f"repair_status={result.state.status}")
    print(f"combined_publishable={str(combined['publishable']).lower()}")
    print(f"combined_headline={combined.get('headline') or 'none'}")
    return 0 if combined["publishable"] else 2


def _validate_source(source_worktree: Path) -> None:
    revision = _git(source_worktree, "rev-parse", "HEAD")
    status = _git(source_worktree, "status", "--porcelain")
    if revision != SOURCE_REVISION or status:
        raise RuntimeError("repair requires the exact clean original Mini-50 source")


def _validate_parent_selection(parent_dir: Path) -> None:
    state = _read_object(parent_dir / "campaign.json")
    records = {
        int(item.get("ordinal") or 0): item
        for item in state.get("records") or []
        if isinstance(item, dict)
    }
    if len(records) != 50:
        raise RuntimeError("parent campaign denominator drift")
    for ordinal, case_id, reason in REPAIR_SELECTION:
        record = records.get(ordinal) or {}
        if record.get("case_id") != case_id:
            raise RuntimeError(f"parent slot {ordinal} identity drift")
        if reason == "provider_transport_error":
            evidence = record.get("evidence") or {}
            if evidence.get("failure_class") != reason:
                raise RuntimeError(
                    f"parent slot {ordinal} is not provider infrastructure"
                )
        elif not (
            record.get("status") == "failed"
            and "strict_pass_at_one_no_rerun" in str(record.get("error") or "")
        ):
            raise RuntimeError(f"parent slot {ordinal} is not an external interruption")


def _configure_environment(args: argparse.Namespace) -> None:
    os.environ["DOCKER_HOST"] = args.docker_host
    harness_root = str(Path(args.swebench_harness_root).resolve())
    if not (Path(harness_root) / "swebench/harness/run_evaluation.py").is_file():
        raise RuntimeError("pinned SWE-bench harness is missing")
    if harness_root not in sys.path:
        sys.path.insert(0, harness_root)
    python_path = [
        item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item
    ]
    if harness_root not in python_path:
        os.environ["PYTHONPATH"] = os.pathsep.join([harness_root, *python_path])


def _build_plan(
    *,
    request: BenchmarkCampaignRequest,
    project_root: Path,
    source_worktree: Path,
    parent_dir: Path,
) -> dict[str, Any]:
    image_manifest_path = parent_dir / "image_manifest.json"
    image_manifest = _read_object(image_manifest_path)
    images = {
        str(item.get("instance_id") or ""): item
        for item in image_manifest.get("images") or []
        if isinstance(item, dict)
    }
    selected_images = []
    for _, case_id, _ in REPAIR_SELECTION:
        image = images.get(case_id)
        if not image:
            raise RuntimeError(f"frozen image is missing for {case_id}")
        live = _inspect_image(str(image.get("tag") or ""))
        expected_platform = f"{image.get('os')}/{image.get('architecture')}"
        if (
            live["image_id"] != image.get("image_id")
            or live["platform"] != expected_platform
            or expected_platform != EXPECTED_IMAGE_PLATFORM
        ):
            raise RuntimeError(f"local image identity drift for {case_id}")
        selected_images.append(
            {
                "case_id": case_id,
                "tag": image.get("tag"),
                "image_id": image.get("image_id"),
                "platform": expected_platform,
            }
        )
    runner_path = Path(__file__).resolve()
    return {
        "schema_version": 1,
        "artifact_type": "mini50_infrastructure_completion_plan",
        "campaign_id": REPAIR_CAMPAIGN_ID,
        "parent_campaign_id": PARENT_CAMPAIGN_ID,
        "frozen_before_repair_outcomes": True,
        "selection_policy": (
            "only external manual interruptions and provider transport failures; "
            "no correctness-unresolved or agent-terminal empty patch reruns"
        ),
        "selected_slots": [
            {"ordinal": ordinal, "case_id": case_id, "reason": reason}
            for ordinal, case_id, reason in REPAIR_SELECTION
        ],
        "selected_case_ids_sha256": _sha256_text(
            "\n".join(case_id for _, case_id, _ in REPAIR_SELECTION)
        ),
        "source": {
            "revision": SOURCE_REVISION,
            "clean": True,
            "worktree_revision": _git(source_worktree, "rev-parse", "HEAD"),
        },
        "campaign_identity": request.identity(),
        "campaign_identity_sha256": _json_sha256(request.identity()),
        "parent_artifacts": {
            name: _sha256_file(parent_dir / name)
            for name in (
                "campaign.json",
                "frozen_plan.json",
                "image_manifest.json",
                "final_publish_gate.json",
            )
        },
        "orchestrator": {
            "path": str(runner_path.relative_to(project_root)),
            "sha256": _sha256_file(runner_path),
        },
        "images": selected_images,
        "replacement_policy": (
            "one new Pass@1 trajectory per selected infrastructure-invalid slot; "
            "resolved, official-unresolved, or agent-terminal empty becomes final"
        ),
        "merge_policy": (
            "retain the original 40 valid Agent outcomes and replace only the 10 "
            "listed infrastructure-invalid slots"
        ),
    }


def _combine_results(
    parent_dir: Path,
    repair_state: CampaignState,
    plan: dict[str, Any],
) -> dict[str, Any]:
    original = _read_object(parent_dir / "campaign.json")
    replacement_by_case = {record.case_id: record for record in repair_state.records}
    selected = {case_id for _, case_id, _ in REPAIR_SELECTION}
    cases: list[dict[str, Any]] = []
    buckets = {
        "official_resolved": 0,
        "official_unresolved": 0,
        "agent_terminal_empty_patch": 0,
        "provider_infra": 0,
        "runtime_infra": 0,
        "evaluator_infra": 0,
        "external_interruption": 0,
    }
    for original_record in original.get("records") or []:
        if not isinstance(original_record, dict):
            continue
        case_id = str(original_record.get("case_id") or "")
        if case_id in selected:
            replacement = replacement_by_case.get(case_id)
            if replacement is None:
                classification = "runtime_infra"
                record = {}
            else:
                record = replacement.to_dict()
                classification = _classify_record(record)
            source = "infrastructure_completion"
        else:
            record = original_record
            classification = _classify_record(record)
            source = "original_mini50"
        buckets[classification] += 1
        evidence = record.get("evidence") or {}
        cases.append(
            {
                "ordinal": int(original_record.get("ordinal") or 0),
                "case_id": case_id,
                "source": source,
                "classification": classification,
                "run_id": record.get("run_id") or "",
                "run_dir": record.get("run_dir") or "",
                "patch_generated": bool(evidence.get("patch_generated")),
                "official_evaluation_status": evidence.get("official_evaluation_status")
                or "not_evaluated",
                "failure_class": evidence.get("failure_class") or "",
            }
        )
    planned = len(cases)
    valid_terminal = (
        buckets["official_resolved"]
        + buckets["official_unresolved"]
        + buckets["agent_terminal_empty_patch"]
    )
    infra = sum(
        buckets[key]
        for key in (
            "provider_infra",
            "runtime_infra",
            "evaluator_infra",
            "external_interruption",
        )
    )
    identity_checks = {
        "parent_campaign_sha256": _sha256_file(parent_dir / "campaign.json")
        == plan["parent_artifacts"]["campaign.json"],
        "repair_source_revision": repair_state.source.get("revision")
        == SOURCE_REVISION,
        "repair_source_clean": not bool(repair_state.source.get("dirty")),
        "repair_config_matches_plan": repair_state.config == plan["campaign_identity"],
        "repair_case_set_exact": set(replacement_by_case) == selected,
    }
    publishable = (
        planned == 50
        and valid_terminal == 50
        and infra == 0
        and all(identity_checks.values())
    )
    return {
        "schema_version": 1,
        "artifact_type": "mini50_infrastructure_completed_result",
        "parent_campaign_id": PARENT_CAMPAIGN_ID,
        "repair_campaign_id": REPAIR_CAMPAIGN_ID,
        "publishable": publishable,
        "headline": f"{buckets['official_resolved']}/50" if publishable else None,
        "planned": planned,
        "terminal_accounted": valid_terminal,
        "official_resolved": buckets["official_resolved"],
        "official_unresolved": buckets["official_unresolved"],
        "agent_terminal_empty_patch": buckets["agent_terminal_empty_patch"],
        "infrastructure": {
            "provider": buckets["provider_infra"],
            "runtime": buckets["runtime_infra"],
            "evaluator": buckets["evaluator_infra"],
            "external_interruption": buckets["external_interruption"],
        },
        "identity_checks": identity_checks,
        "selection_plan_sha256": _json_sha256(plan),
        "cases": sorted(cases, key=lambda item: int(item["ordinal"])),
    }


def _classify_record(record: dict[str, Any]) -> str:
    evidence = record.get("evidence") or {}
    failure = str(evidence.get("failure_class") or "")
    official = str(evidence.get("official_evaluation_status") or "")
    patch_generated = bool(evidence.get("patch_generated"))
    if record.get("status") == "failed":
        return (
            "external_interruption"
            if "strict_pass_at_one" in str(record.get("error") or "")
            else "runtime_infra"
        )
    if failure in INFRA_FAILURES:
        return (
            "provider_infra"
            if failure == "provider_transport_error"
            else "runtime_infra"
        )
    if official == "official_resolved":
        return "official_resolved"
    if official == "official_eval_failed" and patch_generated:
        return "official_unresolved"
    if failure == "official_eval_error" and patch_generated:
        return "evaluator_infra"
    if not patch_generated and _has_agent_terminal_evidence(record):
        return "agent_terminal_empty_patch"
    if failure == "official_eval_error":
        return "evaluator_infra"
    return "runtime_infra"


def _has_agent_terminal_evidence(record: dict[str, Any]) -> bool:
    evidence = record.get("evidence") or {}
    if str(evidence.get("status") or "") not in {"blocked", "completed", "failed"}:
        return False
    run_dir = Path(str(record.get("run_dir") or ""))
    trace_paths = sorted(run_dir.glob("cases/*/trace.json")) if run_dir.is_dir() else []
    if not trace_paths:
        return False
    trace = _read_object(trace_paths[0])
    reason = str(trace.get("stop_reason") or "")
    return bool(reason) and not any(
        marker in reason
        for marker in (
            "provider_transport",
            "invalid_llm_response",
            "cancelled",
            "interrupted",
        )
    )


def _inspect_image(tag: str) -> dict[str, str]:
    process = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            tag,
            "--format",
            "{{.Id}}|{{.Os}}/{{.Architecture}}",
        ],
        text=True,
        capture_output=True,
    )
    if process.returncode != 0:
        raise RuntimeError(f"required local image is unavailable: {tag}")
    image_id, platform = process.stdout.strip().split("|", 1)
    return {"image_id": image_id, "platform": platform}


def _render_report(result: dict[str, Any]) -> str:
    infra = result["infrastructure"]
    rows = "\n".join(
        f"| {item['ordinal']} | `{item['case_id']}` | {item['source']} | "
        f"{item['classification']} | `{item['run_id'] or '-'}` |"
        for item in result["cases"]
    )
    return f"""# Mini-50 v1.1 Infrastructure Completion

## Result

- publishable: `{str(result["publishable"]).lower()}`
- headline: `{result.get("headline") or "not published"}`
- official resolved: `{result["official_resolved"]}/50`
- official unresolved: `{result["official_unresolved"]}/50`
- Agent terminal Empty Patch: `{result["agent_terminal_empty_patch"]}/50`
- remaining infrastructure: provider `{infra["provider"]}`, runtime `{infra["runtime"]}`, evaluator `{infra["evaluator"]}`, external interruption `{infra["external_interruption"]}`

This result retains 40 valid outcomes from the immutable parent campaign and replaces
only the ten pre-classified infrastructure-invalid slots with one new Pass@1 trajectory.

## Case ledger

| # | Case | Evidence source | Final classification | Run ID |
| ---: | --- | --- | --- | --- |
{rows}
"""


def _safe_plan_projection(plan: dict[str, Any], execute: bool) -> dict[str, Any]:
    return {
        "mode": "execute" if execute else "validate_only",
        "campaign_id": plan["campaign_id"],
        "parent_campaign_id": plan["parent_campaign_id"],
        "case_count": len(plan["selected_slots"]),
        "source_revision": plan["source"]["revision"],
        "provider": "opencode-go",
        "model": "deepseek-v4-flash",
        "case_workers": plan["campaign_identity"].get("max_parallel_slots", 1),
        "plan_sha256": _json_sha256(plan),
    }


def _freeze_once(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _json_bytes(payload)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != raw:
            raise RuntimeError("frozen infrastructure completion plan drift")
        return path
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".infrastructure_completion.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def _git(root: Path, *args: str) -> str:
    process = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if process.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed")
    return process.stdout.strip()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
