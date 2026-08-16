#!/usr/bin/env python3
"""补全 Mini-50 v1.1 中仍受 provider 基础设施污染的唯一槽位。"""

from __future__ import annotations

import argparse
import copy
import json
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from agent_forge.atomic_json import atomic_write_json
from agent_forge.bench.api import run_benchmark_campaign
from agent_forge.bench.domain.campaign import BenchmarkCampaignRequest, CampaignState

import run_mini50_infrastructure_completion as round1


ROUND2_CAMPAIGN_ID = "mini50-v1.2-infra-completion-deepseek-v4-flash-3ec537113a"
ROUND1_CAMPAIGN_DIR = (
    ".agent_forge/runs/benchmarks/"
    "swebench-verified-mini-50-infrastructure-completion/"
    f"{round1.REPAIR_CAMPAIGN_ID}"
)
TARGET_CASE = "sympy__sympy-12481"


def main(argv: Sequence[str] | None = None) -> int:
    args = round1.build_parser().parse_args(argv)
    project_root = Path(args.project_root).resolve()
    source_worktree = Path(args.source_worktree).resolve()
    output_root = Path(args.output_root).resolve()
    round1_dir = project_root / ROUND1_CAMPAIGN_DIR
    round1_result_path = round1_dir / "combined_result.json"
    round1_result = round1._read_object(round1_result_path)

    round1._validate_source(source_worktree)
    _validate_round1_result(round1_result)
    round1._configure_environment(args)
    request = _build_request(args)
    plan = _build_plan(
        request=request,
        project_root=project_root,
        source_worktree=source_worktree,
        round1_dir=round1_dir,
        round1_result_path=round1_result_path,
        round1_result=round1_result,
    )
    campaign_dir = output_root / ROUND2_CAMPAIGN_ID
    plan_path = round1._freeze_once(
        campaign_dir / "infrastructure_completion_plan.json", plan
    )
    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "validate_only",
                "campaign_id": ROUND2_CAMPAIGN_ID,
                "parent_campaign_id": round1.REPAIR_CAMPAIGN_ID,
                "case_ids": [TARGET_CASE],
                "provider": "opencode-go",
                "model": "deepseek-v4-flash",
                "source_revision": round1.SOURCE_REVISION,
                "plan_sha256": round1._json_sha256(plan),
            },
            indent=2,
            sort_keys=True,
        )
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
            round1_dir=round1_dir,
            round1_result_path=round1_result_path,
            round1_result=round1_result,
        )
        != plan
    ):
        raise RuntimeError("round-2 source/config/selection/image identity drift")

    result = run_benchmark_campaign(request, project_dir=source_worktree)
    combined = _combine_results(round1_result, result.state, plan)
    atomic_write_json(campaign_dir / "combined_result.json", combined)
    round1._write_text_atomic(
        campaign_dir / "combined_report.md", round1._render_report(combined)
    )
    print(f"campaign_dir={campaign_dir}")
    print(f"repair_status={result.state.status}")
    print(f"combined_publishable={str(combined['publishable']).lower()}")
    print(f"combined_headline={combined.get('headline') or 'none'}")
    return 0 if combined["publishable"] else 2


def _build_request(args: argparse.Namespace) -> BenchmarkCampaignRequest:
    base = round1.build_request(args)
    return replace(
        base,
        case_ids=(TARGET_CASE,),
        campaign_id=ROUND2_CAMPAIGN_ID,
        regression_set="mini50-v1.2-infrastructure-completion",
        max_parallel_slots=1,
    )


def _validate_round1_result(result: dict[str, Any]) -> None:
    cases = result.get("cases") or []
    provider_cases = [
        item.get("case_id")
        for item in cases
        if isinstance(item, dict) and item.get("classification") == "provider_infra"
    ]
    if (
        result.get("artifact_type") != "mini50_infrastructure_completed_result"
        or int(result.get("planned") or 0) != 50
        or provider_cases != [TARGET_CASE]
        or int((result.get("infrastructure") or {}).get("provider") or 0) != 1
    ):
        raise RuntimeError("round-1 result does not contain the exact one-case gap")


def _build_plan(
    *,
    request: BenchmarkCampaignRequest,
    project_root: Path,
    source_worktree: Path,
    round1_dir: Path,
    round1_result_path: Path,
    round1_result: dict[str, Any],
) -> dict[str, Any]:
    round1_plan_path = round1_dir / "infrastructure_completion_plan.json"
    round1_plan = round1._read_object(round1_plan_path)
    image = next(
        (
            item
            for item in round1_plan.get("images") or []
            if isinstance(item, dict) and item.get("case_id") == TARGET_CASE
        ),
        None,
    )
    if image is None:
        raise RuntimeError("round-1 image identity is missing")
    live = round1._inspect_image(str(image.get("tag") or ""))
    if (
        live["image_id"] != image.get("image_id")
        or live["platform"] != image.get("platform")
        or live["platform"] != round1.EXPECTED_IMAGE_PLATFORM
    ):
        raise RuntimeError("round-2 local image identity drift")
    runner_path = Path(__file__).resolve()
    round1_runner_path = (
        project_root / "scripts/run_mini50_infrastructure_completion.py"
    )
    return {
        "schema_version": 1,
        "artifact_type": "mini50_infrastructure_completion_plan",
        "campaign_id": ROUND2_CAMPAIGN_ID,
        "parent_campaign_id": round1.REPAIR_CAMPAIGN_ID,
        "frozen_before_round2_outcome": True,
        "selection_policy": (
            "only the sole provider-infrastructure-invalid slot remaining after "
            "the complete v1.1 round; no correctness or Agent-terminal rerun"
        ),
        "selected_slots": [
            {
                "ordinal": 44,
                "case_id": TARGET_CASE,
                "reason": "provider_transport_error",
            }
        ],
        "source": {
            "revision": round1.SOURCE_REVISION,
            "clean": True,
            "worktree_revision": round1._git(source_worktree, "rev-parse", "HEAD"),
        },
        "campaign_identity": request.identity(),
        "campaign_identity_sha256": round1._json_sha256(request.identity()),
        "parent_artifacts": {
            "round1_plan_sha256": round1._sha256_file(round1_plan_path),
            "round1_result_sha256": round1._sha256_file(round1_result_path),
            "round1_result_semantic_sha256": round1._json_sha256(round1_result),
        },
        "orchestrators": {
            "round2_path": str(runner_path.relative_to(project_root)),
            "round2_sha256": round1._sha256_file(runner_path),
            "round1_path": str(round1_runner_path.relative_to(project_root)),
            "round1_sha256": round1._sha256_file(round1_runner_path),
        },
        "images": [copy.deepcopy(image)],
        "replacement_policy": (
            "one new Pass@1 trajectory for the one remaining provider-invalid "
            "slot; resolved, official-unresolved, or Agent-terminal Empty Patch "
            "becomes final"
        ),
        "merge_policy": (
            "retain all 49 valid outcomes in v1.1 and replace only sympy-12481"
        ),
    }


def _combine_results(
    round1_result: dict[str, Any],
    round2_state: CampaignState,
    plan: dict[str, Any],
) -> dict[str, Any]:
    replacements = {record.case_id: record for record in round2_state.records}
    replacement = replacements.get(TARGET_CASE)
    cases: list[dict[str, Any]] = []
    for previous in round1_result.get("cases") or []:
        if not isinstance(previous, dict):
            continue
        item = copy.deepcopy(previous)
        if item.get("case_id") == TARGET_CASE:
            record = replacement.to_dict() if replacement is not None else {}
            evidence = record.get("evidence") or {}
            item.update(
                {
                    "source": "infrastructure_completion_round2",
                    "classification": round1._classify_record(record),
                    "run_id": record.get("run_id") or "",
                    "run_dir": record.get("run_dir") or "",
                    "patch_generated": bool(evidence.get("patch_generated")),
                    "official_evaluation_status": evidence.get(
                        "official_evaluation_status"
                    )
                    or "not_evaluated",
                    "failure_class": evidence.get("failure_class") or "",
                }
            )
        cases.append(item)

    counts = Counter(str(item.get("classification") or "") for item in cases)
    valid_terminal = sum(
        counts[key]
        for key in (
            "official_resolved",
            "official_unresolved",
            "agent_terminal_empty_patch",
        )
    )
    infrastructure = {
        "provider": counts["provider_infra"],
        "runtime": counts["runtime_infra"],
        "evaluator": counts["evaluator_infra"],
        "external_interruption": counts["external_interruption"],
    }
    identity_checks = {
        "round1_result_sha256": round1._json_sha256(round1_result)
        == plan["parent_artifacts"]["round1_result_semantic_sha256"],
        "round2_source_revision": round2_state.source.get("revision")
        == round1.SOURCE_REVISION,
        "round2_source_clean": not bool(round2_state.source.get("dirty")),
        "round2_config_matches_plan": round2_state.config == plan["campaign_identity"],
        "round2_case_set_exact": set(replacements) == {TARGET_CASE},
    }
    publishable = (
        len(cases) == 50
        and valid_terminal == 50
        and sum(infrastructure.values()) == 0
        and all(identity_checks.values())
    )
    return {
        "schema_version": 1,
        "artifact_type": "mini50_infrastructure_completed_result",
        "parent_campaign_id": round1.PARENT_CAMPAIGN_ID,
        "repair_campaign_ids": [round1.REPAIR_CAMPAIGN_ID, ROUND2_CAMPAIGN_ID],
        "publishable": publishable,
        "headline": f"{counts['official_resolved']}/50" if publishable else None,
        "planned": len(cases),
        "terminal_accounted": valid_terminal,
        "official_resolved": counts["official_resolved"],
        "official_unresolved": counts["official_unresolved"],
        "agent_terminal_empty_patch": counts["agent_terminal_empty_patch"],
        "infrastructure": infrastructure,
        "identity_checks": identity_checks,
        "selection_plan_sha256": round1._json_sha256(plan),
        "cases": sorted(cases, key=lambda item: int(item["ordinal"])),
    }


if __name__ == "__main__":
    raise SystemExit(main())
