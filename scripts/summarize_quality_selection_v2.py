#!/usr/bin/env python3
"""校验并汇总 quality-selection-v2 的完整二十槽正式证据。

Builder 是冻结命令形状的唯一来源；汇总器独立重建动态 evidence plan、重验
生命周期与正式产物，只在完整有效时原子发布胜者。失败时不输出局部成绩。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from agent_forge.bench.adapters.campaign_files import (
    FileCampaignJournal,
    GitSourceIdentity,
)
from agent_forge.bench.application.formal_campaign import (
    audit_completed_formal_campaign,
)
from agent_forge.bench.application.formal_selection import aggregate_formal_winner
from agent_forge.bench.application.quality_selection_v2 import (
    audit_quality_selection_v2_completed_pacing,
    slots_from_manifest,
)
from agent_forge.bench.application.quality_selection_v2_evidence import (
    build_v2_evidence_plan,
)
from agent_forge.bench.application.quality_selection_v2_seal import (
    read_quality_selection_v2_campaign_inputs,
)


BUILDER = Path("scripts/build_quality_selection_v2_manifest.py")
PROTOCOL = Path("benchmarks/showcase/quality-selection-protocol-v2.json")
GOLDEN = Path("benchmarks/regression/golden-10-v2.json")
DEFAULT_MANIFEST = Path(
    "benchmarks/showcase/quality-selection-command-manifest-v2.json"
)
FORMAL_VALUE_FLAGS = {
    "--agent-mode",
    "--base-url",
    "--cases-file",
    "--dataset",
    "--execution-mode",
    "--instance-id",
    "--limit",
    "--max-context-chars",
    "--max-prompt-tokens",
    "--max-revision-rounds",
    "--max-steps",
    "--max-tool-calls-per-turn",
    "--max-workers",
    "--memory-recall-limit",
    "--model",
    "--model-request-timeout-seconds",
    "--model-request-max-attempts",
    "--network-policy",
    "--official-cache-level",
    "--official-namespace",
    "--output-root",
    "--provider",
    "--reasoning-effort",
    "--repo-cache",
    "--reserved-output-tokens",
    "--skills",
    "--temperature",
    "--thinking",
    "--timeout-seconds",
    "--tool-execution-timeout-seconds",
    "--tool-routing",
}
FORMAL_SWITCH_FLAGS = {"--evaluate", "--no-keep-worktree"}
PROBE_VALUE_FLAGS = {
    "--provider",
    "--model",
    "--base-url",
    "--thinking",
    "--reasoning-effort",
    "--timeout",
    "--max-attempts",
    "--output",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ids_sha256(case_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(case_ids).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _resolve_under(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {value}") from exc
    return resolved


def _parse_flags(
    argv: list[str],
    *,
    prefix: list[str],
    value_flags: set[str],
    switch_flags: set[str] | frozenset[str] = frozenset(),
) -> tuple[dict[str, str], set[str]]:
    if argv[: len(prefix)] != prefix:
        raise ValueError("command entrypoint drift")
    values: dict[str, str] = {}
    switches: set[str] = set()
    index = len(prefix)
    while index < len(argv):
        flag = argv[index]
        if "=" in flag or flag not in value_flags | switch_flags:
            raise ValueError(f"unsupported command flag: {flag}")
        if flag in switch_flags:
            if flag in switches:
                raise ValueError(f"duplicate command flag: {flag}")
            switches.add(flag)
            index += 1
            continue
        if flag in values or index + 1 >= len(argv):
            raise ValueError(f"duplicate or valueless command flag: {flag}")
        value = argv[index + 1]
        if value.startswith("--"):
            raise ValueError(f"missing command value: {flag}")
        values[flag] = value
        index += 2
    return values, switches


def _load_builder(root: Path) -> ModuleType:
    path = root / BUILDER
    spec = importlib.util.spec_from_file_location(
        "quality_selection_v2_bound_builder", path
    )
    if spec is None or spec.loader is None:
        raise ValueError("cannot load bound v2 manifest builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_cohort_provenance(root: Path, golden: dict[str, Any]) -> None:
    case_ids = [str(item) for item in golden.get("case_ids") or []]
    selected = golden.get("selected_cases")
    provenance = golden.get("selection_provenance")
    if (
        len(case_ids) != 10
        or len(set(case_ids)) != 10
        or not isinstance(selected, list)
        or [item.get("instance_id") for item in selected] != case_ids
        or golden.get("ordered_case_ids_sha256") != _ids_sha256(case_ids)
        or not isinstance(provenance, dict)
        or provenance.get("allowed_fields") != ["instance_id", "repo"]
        or provenance.get("outcome_blind") is not True
        or provenance.get("old_quality_selection_artifacts_used") is not False
    ):
        raise ValueError("Golden-10 v2 cohort provenance drift")
    seed = str(provenance.get("seed") or "")
    for item in selected:
        instance_id = str(item.get("instance_id") or "")
        digest = hashlib.sha256(f"{seed}:{instance_id}".encode("utf-8")).hexdigest()
        if item.get("rank_sha256") != digest:
            raise ValueError("Golden-10 v2 selected rank drift")
    pool = provenance.get("remaining_pool")
    if not isinstance(pool, dict):
        raise ValueError("Golden-10 v2 exclusion provenance drift")
    sources = pool.get("exclusion_sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("Golden-10 v2 exclusion provenance drift")
    combined: set[str] = set()
    for claim in sources:
        path = _resolve_under(root, str(claim.get("path") or ""))
        source = _read_json(path)
        source_ids = [str(item) for item in source.get("case_ids") or []]
        if (
            claim.get("file_sha256") != _sha256(path)
            or claim.get("case_count") != len(source_ids)
            or claim.get("ordered_case_ids_sha256") != _ids_sha256(source_ids)
        ):
            raise ValueError("Golden-10 v2 exclusion source drift")
        combined.update(source_ids)
    ordered = sorted(combined)
    if pool.get("combined_exclusion_count") != len(ordered) or pool.get(
        "combined_exclusion_ordered_case_ids_sha256"
    ) != _ids_sha256(ordered):
        raise ValueError("Golden-10 v2 combined exclusion drift")


def _validate_dataset_binding(
    root: Path,
    manifest: dict[str, Any],
    golden: dict[str, Any],
) -> None:
    artifact = _resolve_under(root, str(manifest.get("artifact_root") or ""))
    binding_path = artifact / "dataset-binding.json"
    agent_path = artifact / "dataset" / "agent-cases.json"
    official_path = artifact / "dataset" / "official-cases.json"
    binding = _read_json(binding_path)
    case_ids = [str(item) for item in golden.get("case_ids") or []]
    provenance = golden.get("selection_provenance")
    dataset = provenance.get("dataset") if isinstance(provenance, dict) else None
    expected = {
        "schema_version": 1,
        "status": "mechanically_exported_no_hidden_values_printed",
        "row_count": len(case_ids),
        "ordered_case_ids": case_ids,
        "manifest_path": str((root / GOLDEN).resolve()),
        "manifest_sha256": _sha256(root / GOLDEN),
        "exporter_sha256": _sha256(root / "scripts/export_showcase_datasets.py"),
        "arrow_sha256": dataset.get("arrow_sha256")
        if isinstance(dataset, dict)
        else None,
        "agent_output": str(agent_path.resolve()),
        "official_output": str(official_path.resolve()),
        "agent_sha256": _sha256(agent_path),
        "official_sha256": _sha256(official_path),
        "agent_fields": [
            "instance_id",
            "repo",
            "problem_statement",
            "base_commit",
            "version",
            "environment_setup_commit",
        ],
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        raise ValueError("quality-selection-v2 dataset binding drift")
    if (
        manifest.get("dataset_binding_sha256") != _sha256(binding_path)
        or manifest.get("agent_dataset_sha256") != expected["agent_sha256"]
        or manifest.get("official_dataset_sha256") != expected["official_sha256"]
    ):
        raise ValueError("quality-selection-v2 dataset hash binding drift")


def _validate_command_syntax(root: Path, manifest: dict[str, Any]) -> None:
    artifact = Path(manifest["artifact_root"])
    fixed = manifest["fixed_argv"]
    dynamic = {"--instance-id", "--limit", "--model", "--output-root"}
    values, switches = _parse_flags(
        fixed,
        prefix=[".venv/bin/forge", "bench", "swebench"],
        value_flags=FORMAL_VALUE_FLAGS - dynamic,
        switch_flags=FORMAL_SWITCH_FLAGS,
    )
    if set(values) != FORMAL_VALUE_FLAGS - dynamic or switches != FORMAL_SWITCH_FLAGS:
        raise ValueError("v2 fixed command flag set drift")
    for flag in ("--dataset", "--cases-file", "--repo-cache"):
        _resolve_under(root, values[flag])
    for item in manifest["commands"]:
        combined = [*fixed, *item["argv_suffix"]]
        values, switches = _parse_flags(
            combined,
            prefix=[".venv/bin/forge", "bench", "swebench"],
            value_flags=FORMAL_VALUE_FLAGS,
            switch_flags=FORMAL_SWITCH_FLAGS,
        )
        if set(values) != FORMAL_VALUE_FLAGS or switches != FORMAL_SWITCH_FLAGS:
            raise ValueError("v2 formal command flag set drift")
        _resolve_under(root, values["--output-root"])
    for item in manifest["capability_probes"]:
        values, switches = _parse_flags(
            item["argv"],
            prefix=[".venv/bin/python", "scripts/probe_model_tool_contract.py"],
            value_flags=PROBE_VALUE_FLAGS,
        )
        if set(values) != PROBE_VALUE_FLAGS or switches:
            raise ValueError("v2 capability probe flag set drift")
        _resolve_under(root, values["--output"])
    qualification_flags = PROBE_VALUE_FLAGS | {
        "--round-trips",
        "--capability-preflight",
    }
    for item in manifest["qualification_commands"]:
        values, switches = _parse_flags(
            item["argv"],
            prefix=[".venv/bin/python", "scripts/probe_model_rate_limit_contract.py"],
            value_flags=qualification_flags,
        )
        if set(values) != qualification_flags or switches:
            raise ValueError("v2 qualification flag set drift")
        _resolve_under(root, values["--capability-preflight"])
        _resolve_under(root, values["--output"])
    path_fields = (
        "readiness_path",
        "image_seal_state_path",
        "campaign_inputs_path",
        "campaign_state_root",
        "summary_output_path",
        "ledger_path",
    )
    for field in path_fields:
        path = _resolve_under(root, manifest[field])
        if path.parent != (root / artifact).resolve():
            raise ValueError(f"v2 {field} location drift")
    if manifest.get("campaign_id") != "showcase-quality-selection-v2":
        raise ValueError("v2 campaign identity drift")
    if (
        manifest.get("preflight_ledger_last_sequence") != 20
        or manifest.get("completed_ledger_last_sequence") != 80
    ):
        raise ValueError("v2 pacing-ledger denominator drift")


def validate_preregistration(
    project_root: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    root = project_root.resolve()
    manifest = _read_json(_resolve_under(root, manifest_path))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("manifest_id") != "showcase-quality-v2-selection-commands"
        or manifest.get("status")
        != "content_preregistered_source_tag_pending_launch_verification"
    ):
        raise ValueError("not a preregistered quality-selection-v2 manifest")
    source = manifest.get("source_identity")
    tag = str(source.get("expected_tag") or "") if isinstance(source, dict) else ""
    if not tag or any(character.isspace() for character in tag):
        raise ValueError("v2 expected source tag is invalid")
    artifact_path = _resolve_under(root, str(manifest.get("artifact_root") or ""))
    artifact = artifact_path.relative_to(root)
    if artifact.parts[:1] != (".agent_forge",):
        raise ValueError("v2 artifact root must stay under .agent_forge")
    expected = _load_builder(root).compose_manifest(root, artifact, tag)
    if manifest != expected:
        keys = sorted(
            key
            for key in set(manifest) | set(expected)
            if manifest.get(key) != expected.get(key)
        )
        raise ValueError("v2 generated manifest drift: " + ", ".join(keys))
    _validate_command_syntax(root, manifest)
    golden = _read_json(root / GOLDEN)
    _validate_cohort_provenance(root, golden)
    _validate_dataset_binding(root, manifest, golden)
    protocol = _read_json(root / PROTOCOL)
    qualification = protocol.get("provider_capacity_qualification", {})
    infrastructure = protocol.get("provider_infrastructure_policy", {})
    if (
        qualification.get("enters_formal_denominator") is not False
        or protocol.get("execution", {}).get("correctness_rerun") != 0
        or infrastructure.get("partial_candidate_comparison_allowed") is not False
    ):
        raise ValueError("v2 publication policy drift")
    return manifest


def summarize(project_root: Path, manifest_path: Path) -> dict[str, Any]:
    """完整重验二十槽后发布唯一胜者；任何前置失败都不写 summary。"""

    root = project_root.resolve()
    manifest = validate_preregistration(project_root, manifest_path)
    manifest_file = _resolve_under(root, manifest_path)
    campaign_inputs = read_quality_selection_v2_campaign_inputs(
        project_root=root,
        manifest_path=manifest_file,
        manifest=manifest,
        campaign_inputs_path=Path(manifest["campaign_inputs_path"]),
        source_reader=GitSourceIdentity(root),
    )
    plan = build_v2_evidence_plan(
        root,
        manifest_file,
        manifest,
        campaign_inputs.campaign_inputs_path,
    )
    ledger_sha256 = audit_quality_selection_v2_completed_pacing(
        ledger_path=_resolve_under(root, manifest["ledger_path"]),
        slots=slots_from_manifest(manifest, root),
        prefix_sha256=plan.pacing_ledger_prefix_sha256,
        prefix_bytes=campaign_inputs.pacing_ledger_prefix_bytes,
        prefix_last_sequence=int(manifest["preflight_ledger_last_sequence"]),
        completed_last_sequence=int(manifest["completed_ledger_last_sequence"]),
        minimum_seconds=int(
            manifest["pacing"]["minimum_seconds_between_provider_commands"]
        ),
    )
    records = audit_completed_formal_campaign(
        journal=FileCampaignJournal(root),
        state_root=manifest["campaign_state_root"],
        campaign_id=plan.campaign_id,
        identity_sha256=plan.identity_sha256,
        slots=plan.slots,
        expected_launch_source=plan.expected_launch_source,
    )
    selection = aggregate_formal_winner(
        records, plan.expected_slots, plan.candidate_order
    )
    if selection["status"] != "winner_selected" or selection["winner"] is None:
        raise RuntimeError("quality-selection v2 has no protocol-valid winner")
    usage_tokens = {candidate: 0 for candidate in plan.candidate_order}
    usage_cost = {candidate: Decimal("0") for candidate in plan.candidate_order}
    for record, slot in zip(records, plan.expected_slots, strict=True):
        run = record.validated
        if run is None:
            raise RuntimeError("quality-selection v2 validated record is missing")
        usage_tokens[slot.candidate_id] += run.tokens
        usage_cost[slot.candidate_id] += run.cost
    usage = {
        candidate: {
            "tokens": usage_tokens[candidate],
            "estimated_cost_usd": str(usage_cost[candidate]),
        }
        for candidate in plan.candidate_order
    }
    winner = selection["winner"]
    selected_probe = next(
        item for item in manifest["capability_probes"] if item["candidate_id"] == winner
    )
    selected_argv = selected_probe["argv"]
    payload = {
        "schema_version": 1,
        "artifact_type": "quality_selection_v2_summary",
        "status": "winner_selected",
        "purpose": "Golden-10 v2 development-profile selection only",
        "manifest_sha256": _sha256(manifest_file),
        "campaign_inputs_sha256": campaign_inputs.campaign_inputs_sha256,
        "campaign_identity_sha256": plan.identity_sha256,
        "completed_pacing_ledger_sha256": ledger_sha256,
        "planned_starts": 20,
        "validated_starts": 20,
        "winner": winner,
        "selected_profile": {
            "provider": _flag_from_argv(selected_argv, "--provider"),
            "requested_model": _flag_from_argv(selected_argv, "--model"),
            "observed_model": campaign_inputs.candidate_observed_models[winner],
            "max_steps": int(_flag_from_argv(manifest["fixed_argv"], "--max-steps")),
            "model_request_max_attempts": int(
                _flag_from_argv(manifest["fixed_argv"], "--model-request-max-attempts")
            ),
            "cost_budget_usd": None,
        },
        "selection": selection,
        "supporting_usage": usage,
        "claim_limit": (
            "Golden-10 v2 is a frozen development set used to select the current "
            "profile; it is not a SWE-bench Verified resolved-rate estimate."
        ),
    }
    output = _resolve_under(root, manifest["summary_output_path"])
    if not FileCampaignJournal(root).write_once(output, payload):
        raise RuntimeError("quality-selection v2 summary already exists")
    return payload


def _flag_from_argv(argv: list[str], flag: str) -> str:
    positions = [index for index, token in enumerate(argv) if token == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise RuntimeError(f"quality-selection v2 summary flag drift: {flag}")
    return str(argv[positions[0] + 1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        manifest = validate_preregistration(args.project_root, args.manifest)
        print(
            json.dumps(
                {
                    "manifest_id": manifest["manifest_id"],
                    "planned_starts": manifest["planned_starts"],
                    "valid": True,
                },
                sort_keys=True,
            )
        )
        return
    try:
        result = summarize(args.project_root, args.manifest)
    except Exception as exc:  # noqa: BLE001 - CLI 只发布粗粒度失败，不泄露局部指标。
        print(
            json.dumps(
                {
                    "status": "invalid_no_winner",
                    "reason_code": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        raise SystemExit(2) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
