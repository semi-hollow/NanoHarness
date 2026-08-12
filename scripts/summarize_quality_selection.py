#!/usr/bin/env python3
"""Mechanically select the frozen Golden-10 quality profile.

This program is deliberately outcome-blind beyond the official aggregate
buckets.  It never opens the sealed dataset, gold patches, per-test output, or
the official per-case ``report.json`` (which contains ``tests_status``).  It
hashes the sealed dataset as opaque bytes and consumes only run identity,
usage/trace metadata, candidate/prediction/evaluator patch bytes, scorecards,
and the safe official run aggregate.

Any incomplete candidate, infrastructure failure, configuration/identity
drift, or patch-byte mismatch refuses the whole selection.  A winner is
written only after both candidates pass every validation gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent_forge.bench.formal_artifacts import (
    FormalArtifactRefused as SelectionRefused,
    FormalRunExpectation,
    _as_int,
    _read_json,
    _refuse,
    RETRYABLE_TRANSPORT_ERROR_CODES,
    _safe_official_aggregate,  # noqa: F401 - retained for test/API compatibility
    _sha256_bytes,
    _sha256_file,
    parse_formal_cli,
    validate_formal_run,
)


PROTOCOL_PATH = Path("benchmarks/showcase/quality-selection-protocol-v1.json")
COMMAND_MANIFEST_PATH = Path(
    "benchmarks/showcase/quality-selection-command-manifest-v1.json"
)
IMAGE_MANIFEST_PATH = Path(
    "benchmarks/showcase/quality-selection-image-manifest-v1.json"
)
PROBE_SCRIPT_PATH = Path("scripts/probe_model_tool_contract.py")
SUMMARIZER_SCRIPT_PATH = Path("scripts/summarize_quality_selection.py")
DEFAULT_ARTIFACT_ROOT = Path(".agent_forge/canonical-showcase/quality-selection")
DEFAULT_OUTPUT_NAME = "selection-summary.json"


@dataclass(frozen=True)
class CandidateIdentity:
    candidate_id: str
    provider: str
    model: str
    observed_model: str
    preflight_sha256: str


@dataclass(frozen=True)
class CommandPlan:
    candidate_id: str
    shard: str
    instance_ids: tuple[str, ...]
    output_root: Path
    base_url: str
    argv: tuple[str, ...]


@dataclass
class CandidateMetrics:
    candidate_id: str
    provider: str
    model: str
    observed_model: str
    preflight_sha256: str
    planned: int = 0
    finalized: int = 0
    official_resolved: int = 0
    official_unresolved: int = 0
    official_decided: int = 0
    empty_patches: int = 0
    infrastructure_failures: int = 0
    failed_tool_calls: int = 0
    provider_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    run_ids: list[str] | None = None
    evidence: list[dict[str, Any]] | None = None
    patch_binding_parts: list[bytes] | None = None

    def __post_init__(self) -> None:
        self.run_ids = [] if self.run_ids is None else self.run_ids
        self.evidence = [] if self.evidence is None else self.evidence
        self.patch_binding_parts = (
            [] if self.patch_binding_parts is None else self.patch_binding_parts
        )


def _resolve_from_project(project_root: Path, raw_path: str, label: str) -> Path:
    _refuse(bool(raw_path), f"{label} path is empty")
    path = Path(raw_path)
    resolved = (path if path.is_absolute() else project_root / path).resolve()
    return resolved


def _ordered_ids_sha256(case_ids: list[str]) -> str:
    return _sha256_bytes("\n".join(case_ids).encode("utf-8"))


def _safe_endpoint_identity(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    endpoint = f"{parsed.scheme}://{parsed.hostname or ''}"
    if parsed.port:
        endpoint += f":{parsed.port}"
    endpoint += parsed.path.rstrip("/")
    return endpoint, _sha256_bytes(base_url.encode("utf-8"))


def _normalized_command_hash(
    commands: list[dict[str, Any]], remove_flags: set[str]
) -> str:
    normalized: list[list[str]] = []
    for command in commands:
        argv = command.get("argv")
        _refuse(isinstance(argv, list), "command argv must be a list")
        result: list[str] = []
        index = 0
        while index < len(argv):
            token = argv[index]
            if token in remove_flags:
                _refuse(index + 1 < len(argv), f"missing normalized value for {token}")
                index += 2
                continue
            result.append(token)
            index += 1
        normalized.append(result)
    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(serialized)


def _validate_capability_probe_commands(
    project_root: Path,
    artifact_root: Path,
    command_manifest: dict[str, Any],
    candidates: list[dict[str, str]],
    runtime: dict[str, Any],
    base_url: str,
) -> None:
    probes_value = command_manifest.get("capability_probes")
    _refuse(isinstance(probes_value, list), "capability probe commands are missing")
    _refuse(len(probes_value) == len(candidates), "capability probe count drift")
    candidate_by_id = {item["candidate_id"]: item for item in candidates}
    expected_order = [item["candidate_id"] for item in candidates]
    actual_order: list[str] = []
    for index, probe in enumerate(probes_value):
        label = f"capability_probe[{index}]"
        _refuse(isinstance(probe, dict), f"{label} must be an object")
        candidate_id = str(probe.get("candidate_id") or "")
        _refuse(candidate_id in candidate_by_id, f"{label} candidate drift")
        actual_order.append(candidate_id)
        _refuse(
            probe.get("output_must_be_absent") is True,
            f"{label} output-absence precondition drift",
        )
        argv = probe.get("argv")
        _refuse(isinstance(argv, list), f"{label} argv must be a list")
        _refuse(
            argv[:2] == [".venv/bin/python", "scripts/probe_model_tool_contract.py"],
            f"{label} entrypoint drift",
        )
        values: dict[str, str] = {}
        allowed_flags = {
            "--provider",
            "--model",
            "--base-url",
            "--thinking",
            "--reasoning-effort",
            "--timeout",
            "--output",
        }
        cursor = 2
        while cursor < len(argv):
            flag = argv[cursor]
            _refuse(flag in allowed_flags, f"{label} unsupported flag {flag}")
            _refuse(cursor + 1 < len(argv), f"{label} missing value for {flag}")
            _refuse(flag not in values, f"{label} repeats {flag}")
            value = argv[cursor + 1]
            _refuse(isinstance(value, str), f"{label} flag value must be text")
            values[flag] = value
            cursor += 2
        _refuse(set(values) == allowed_flags, f"{label} flag set drift")
        candidate = candidate_by_id[candidate_id]
        expected_values = {
            "--provider": candidate["provider"],
            "--model": candidate["model"],
            "--base-url": base_url,
            "--thinking": str(runtime["thinking_mode"]),
            "--reasoning-effort": str(runtime["reasoning_effort"]),
            "--timeout": str(runtime["model_request_timeout_seconds"]),
        }
        for flag, expected in expected_values.items():
            _refuse(values.get(flag) == expected, f"{label} {flag} drift")
        output_path = _resolve_from_project(
            project_root, values["--output"], f"{label} output"
        )
        _refuse(
            output_path
            == (artifact_root / "preflight" / f"{candidate_id}.json").resolve(),
            f"{label} output path drift",
        )
    _refuse(actual_order == expected_order, "capability probe order drift")

    credential = command_manifest.get("credential_preflight")
    _refuse(isinstance(credential, dict), "credential preflight contract is missing")
    _refuse(
        credential.get("launcher_shell") == "zsh -lic"
        and credential.get("required_present_nonempty") == "OPENCODE_GO_API_KEY"
        and credential.get("resolver_required_credential_source")
        == "OPENCODE_GO_API_KEY"
        and credential.get("record_key_value") is False,
        "OpenCode Go credential contract drift",
    )
    forbidden = credential.get("forbidden_fallback_sources")
    _refuse(
        isinstance(forbidden, list)
        and set(forbidden) == {"DEEPSEEK_API_KEY", "OPENAI_API_KEY"},
        "credential fallback boundary drift",
    )


def _validate_preregistration(
    project_root: Path,
    artifact_root: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[str],
    list[CommandPlan],
    dict[str, str],
]:
    protocol_path = project_root / PROTOCOL_PATH
    command_manifest_path = project_root / COMMAND_MANIFEST_PATH
    protocol = _read_json(protocol_path, "quality-selection protocol")
    command_manifest = _read_json(command_manifest_path, "command manifest")

    protocol_sha256 = _sha256_file(protocol_path, "quality-selection protocol")
    _refuse(
        command_manifest.get("protocol_sha256") == protocol_sha256,
        "command manifest is not bound to the current protocol",
    )
    _refuse(
        command_manifest.get("capability_probe_script_sha256")
        == _sha256_file(project_root / PROBE_SCRIPT_PATH, "capability probe script"),
        "capability probe script hash drift",
    )
    _refuse(
        command_manifest.get("selection_summarizer_script_sha256")
        == _sha256_file(project_root / SUMMARIZER_SCRIPT_PATH, "selection summarizer"),
        "selection summarizer script hash drift",
    )
    _refuse(protocol.get("schema_version") == 1, "unsupported protocol schema")
    _refuse(
        command_manifest.get("schema_version") == 1,
        "unsupported command-manifest schema",
    )
    _refuse(
        command_manifest.get("status")
        == "frozen_before_any_quality_selection_model_call",
        "command manifest is not frozen",
    )

    development = protocol.get("development_set")
    _refuse(isinstance(development, dict), "protocol development_set is missing")
    golden_path = _resolve_from_project(
        project_root,
        str(development.get("manifest") or ""),
        "development-set manifest",
    )
    _refuse(
        _sha256_file(golden_path, "development-set manifest")
        == command_manifest.get("development_set_manifest_sha256"),
        "development-set manifest hash drift",
    )
    golden = _read_json(golden_path, "development-set manifest")
    raw_case_ids = golden.get("case_ids")
    _refuse(isinstance(raw_case_ids, list), "development-set case_ids are missing")
    case_ids = [str(item) for item in raw_case_ids]
    _refuse(all(case_ids), "development-set contains an empty case id")
    _refuse(len(case_ids) == len(set(case_ids)), "development-set contains duplicates")
    _refuse(
        _ordered_ids_sha256(case_ids) == golden.get("ordered_case_ids_sha256"),
        "development-set ordered id hash drift",
    )
    per_candidate = _as_int(
        development.get("planned_cases_per_candidate"),
        "planned cases per candidate",
    )
    _refuse(len(case_ids) == per_candidate, "development-set denominator drift")

    commands_value = command_manifest.get("commands")
    _refuse(isinstance(commands_value, list), "command manifest commands are missing")
    commands: list[dict[str, Any]] = []
    for value in commands_value:
        _refuse(isinstance(value, dict), "command manifest entry must be an object")
        commands.append(value)
    candidates_value = protocol.get("candidates")
    _refuse(isinstance(candidates_value, list), "protocol candidates are missing")
    candidates: list[dict[str, str]] = []
    for value in candidates_value:
        _refuse(isinstance(value, dict), "candidate identity must be an object")
        candidate = {
            "candidate_id": str(value.get("candidate_id") or ""),
            "provider": str(value.get("provider") or ""),
            "model": str(value.get("model") or ""),
        }
        _refuse(all(candidate.values()), "candidate identity is incomplete")
        candidates.append(candidate)
    candidate_order = [item["candidate_id"] for item in candidates]
    _refuse(
        candidate_order
        == list(protocol.get("execution", {}).get("candidate_order") or []),
        "candidate order drift",
    )
    _refuse(
        len(candidate_order) == len(set(candidate_order)) == 2,
        "quality selection requires exactly two unique candidates",
    )

    normalization = command_manifest.get("normalization")
    _refuse(isinstance(normalization, dict), "command normalization is missing")
    remove_flags_value = normalization.get("remove_flag_value_pairs")
    _refuse(isinstance(remove_flags_value, list), "normalization flags are missing")
    remove_flags = {str(item) for item in remove_flags_value}
    _refuse(
        remove_flags == {"--model", "--limit", "--instance-id", "--output-root"},
        "normalization boundary drift",
    )
    _refuse(
        _normalized_command_hash(commands, remove_flags)
        == command_manifest.get("normalized_fixed_argv_sha256"),
        "normalized command hash drift",
    )

    runtime = protocol.get("fixed_runtime")
    _refuse(isinstance(runtime, dict), "protocol fixed_runtime is missing")
    skill_spec = str(runtime.get("skill") or "")
    _refuse("@" in skill_spec, "protocol skill identity is incomplete")
    skill_name, skill_version = skill_spec.rsplit("@", 1)
    _refuse(bool(skill_name and skill_version), "protocol skill identity is incomplete")
    candidate_by_id = {item["candidate_id"]: item for item in candidates}

    plans: list[CommandPlan] = []
    concatenated: dict[str, list[str]] = {item: [] for item in candidate_order}
    shard_shapes: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        item: [] for item in candidate_order
    }
    common_base_url = ""
    for command_index, command in enumerate(commands):
        label = f"command[{command_index}]"
        candidate_id = str(command.get("candidate_id") or "")
        _refuse(candidate_id in candidate_by_id, f"{label} has unknown candidate")
        shard = str(command.get("shard") or "")
        _refuse(bool(shard), f"{label} shard is missing")
        raw_command_ids = command.get("instance_ids")
        _refuse(isinstance(raw_command_ids, list), f"{label} instance_ids are missing")
        command_ids = [str(item) for item in raw_command_ids]
        raw_argv = command.get("argv")
        _refuse(
            isinstance(raw_argv, list)
            and all(isinstance(item, str) for item in raw_argv),
            f"{label} argv must contain only strings",
        )
        argv = tuple(raw_argv)
        parsed = parse_formal_cli(argv, label)
        _refuse(
            parsed["instance_id"] == command_ids,
            f"{label} argv instance order drift",
        )
        _refuse(len(command_ids) > 0, f"{label} has no cases")

        identity = candidate_by_id[candidate_id]
        expected_cli = {
            "provider": identity["provider"],
            "model": identity["model"],
            "agent_mode": runtime["agent_mode"],
            "thinking_mode": runtime["thinking_mode"],
            "reasoning_effort": runtime["reasoning_effort"],
            "tool_routing": runtime["tool_routing_mode"],
            "skills": skill_name,
            "execution_mode": runtime["execution_mode"],
            "network_policy": runtime["network_policy"],
            "official_namespace": runtime["official_namespace"],
            "official_cache_level": runtime["official_cache_level"],
            "temperature": runtime["temperature"],
            "max_steps": runtime["max_steps"],
            "max_context_chars": runtime["max_context_chars"],
            "max_prompt_tokens": runtime["max_prompt_tokens"],
            "reserved_output_tokens": runtime["reserved_output_tokens"],
            "max_tool_calls_per_turn": runtime["max_tool_calls_per_turn"],
            "timeout_seconds": runtime["run_timeout_seconds_per_case"],
            "model_request_timeout_seconds": runtime["model_request_timeout_seconds"],
            "tool_execution_timeout_seconds": runtime["tool_execution_timeout_seconds"],
            "max_workers": runtime["official_max_workers_per_shard"],
            "memory_recall_limit": runtime["memory_recall_limit"],
            "cost_budget_usd": runtime.get("cost_budget_usd_per_case"),
            "max_revision_rounds": 0,
            "limit": len(command_ids),
            "evaluate": True,
            "keep_worktree": False,
            "namespace_empty": False,
        }
        for key, expected in expected_cli.items():
            _refuse(parsed.get(key) == expected, f"{label} effective {key} drift")

        base_url = str(parsed.get("base_url") or "")
        _refuse(bool(base_url), f"{label} base URL is missing")
        common_base_url = common_base_url or base_url
        _refuse(base_url == common_base_url, f"{label} base URL drift")

        dataset_path = _resolve_from_project(
            project_root, str(parsed.get("dataset") or ""), f"{label} dataset"
        )
        cases_path = _resolve_from_project(
            project_root, str(parsed.get("cases_file") or ""), f"{label} cases file"
        )
        expected_dataset = (artifact_root / "dataset" / "official-cases.json").resolve()
        expected_cases = (artifact_root / "dataset" / "agent-cases.json").resolve()
        _refuse(
            dataset_path == expected_dataset, f"{label} official dataset path drift"
        )
        _refuse(cases_path == expected_cases, f"{label} agent dataset path drift")
        _refuse(bool(parsed.get("repo_cache")), f"{label} repo cache is missing")

        output_root = _resolve_from_project(
            project_root,
            str(parsed.get("output_root") or ""),
            f"{label} output root",
        )
        expected_output = (artifact_root / candidate_id / shard).resolve()
        _refuse(output_root == expected_output, f"{label} output root drift")
        plans.append(
            CommandPlan(
                candidate_id=candidate_id,
                shard=shard,
                instance_ids=tuple(command_ids),
                output_root=output_root,
                base_url=base_url,
                argv=argv,
            )
        )
        concatenated[candidate_id].extend(command_ids)
        shard_shapes[candidate_id].append((shard, tuple(command_ids)))

    _validate_capability_probe_commands(
        project_root,
        artifact_root,
        command_manifest,
        candidates,
        runtime,
        common_base_url,
    )
    execution_schedule = command_manifest.get("execution_schedule")
    _refuse(isinstance(execution_schedule, dict), "execution schedule is missing")
    _refuse(
        execution_schedule.get("mode") == "serial_manifest_order"
        and execution_schedule.get("max_concurrent_commands") == 1,
        "execution schedule is not serial manifest order",
    )
    actual_schedule = [f"{plan.candidate_id}/{plan.shard}" for plan in plans]
    _refuse(
        execution_schedule.get("command_order") == actual_schedule,
        "command order is not bound to execution_schedule",
    )
    for candidate_id in candidate_order:
        _refuse(
            concatenated[candidate_id] == case_ids,
            f"{candidate_id} does not exactly cover the development set in order",
        )
    _refuse(
        shard_shapes[candidate_order[0]] == shard_shapes[candidate_order[1]],
        "candidate shard shapes differ",
    )
    planned_starts = sum(len(plan.instance_ids) for plan in plans)
    _refuse(
        planned_starts == command_manifest.get("planned_starts"),
        "planned-start denominator drift",
    )
    _refuse(
        planned_starts == development.get("planned_total_starts"),
        "protocol total-start denominator drift",
    )
    execution = protocol.get("execution") or {}
    expected_execution = {
        "pass_at": 1,
        "cross_shard_concurrency": 1,
        "correctness_rerun": 0,
        "whole_case_provider_retry": 0,
        "built_in_identical_transport_attempts_per_llm_call": 2,
        "official_evaluator_rerun": 0,
        "result_driven_parameter_change": False,
    }
    for key, expected in expected_execution.items():
        _refuse(execution.get(key) == expected, f"protocol execution {key} drift")
    _refuse(
        runtime.get("fallback_allowed") is False,
        "protocol unexpectedly permits fallback",
    )
    selection_rule = protocol.get("selection_rule")
    _refuse(isinstance(selection_rule, dict), "protocol selection rule is missing")
    _refuse(
        selection_rule.get("primary")
        == (
            "highest official_resolved over the fixed planned denominator of "
            f"{per_candidate}"
        ),
        "protocol primary selection rule drift",
    )
    frozen_tie_breaks = [
        "higher official_decided coverage",
        "fewer empty patches",
        "fewer provider or evaluator infrastructure failures",
        "fewer failed tool calls",
        "candidate_order",
    ]
    _refuse(
        selection_rule.get("tie_break_order") == frozen_tie_breaks,
        "protocol tie-break order drift",
    )
    _refuse(
        selection_rule.get("validity_requirement")
        == "both candidates must complete and remain protocol-valid",
        "protocol candidate-validity rule drift",
    )

    opaque_assets = {
        "dataset_binding_sha256": artifact_root / "dataset-binding.json",
        "agent_dataset_sha256": artifact_root / "dataset" / "agent-cases.json",
        "official_dataset_sha256": artifact_root / "dataset" / "official-cases.json",
        "image_manifest_sha256": project_root / IMAGE_MANIFEST_PATH,
        "skill_file_sha256": (
            project_root
            / "agent_forge"
            / "skills"
            / "packages"
            / skill_name
            / "SKILL.md"
        ),
    }
    for manifest_key, path in opaque_assets.items():
        _refuse(
            _sha256_file(path, manifest_key) == command_manifest.get(manifest_key),
            f"{manifest_key} drift",
        )

    hashes = {
        "protocol_sha256": protocol_sha256,
        "command_manifest_sha256": _sha256_file(
            command_manifest_path, "command manifest"
        ),
        "development_set_manifest_sha256": str(
            command_manifest["development_set_manifest_sha256"]
        ),
    }
    return protocol, command_manifest, case_ids, plans, hashes


def _validate_preflight(
    artifact_root: Path,
    protocol: dict[str, Any],
    command_manifest: dict[str, Any],
    plan: CommandPlan,
    candidate: dict[str, str],
) -> CandidateIdentity:
    candidate_id = candidate["candidate_id"]
    path = artifact_root / "preflight" / f"{candidate_id}.json"
    preflight = _read_json(path, f"{candidate_id} preflight")
    runtime = protocol["fixed_runtime"]
    endpoint_origin_path, base_url_sha256 = _safe_endpoint_identity(plan.base_url)
    credential_preflight = command_manifest.get("credential_preflight")
    _refuse(
        isinstance(credential_preflight, dict),
        "credential preflight contract is missing",
    )
    expected = {
        "schema_version": 1,
        "status": "passed",
        "provider": candidate["provider"],
        "requested_model": candidate["model"],
        "credential_source": credential_preflight.get(
            "resolver_required_credential_source"
        ),
        "base_url_origin_path": endpoint_origin_path,
        "base_url_sha256": base_url_sha256,
        "thinking_mode": runtime["thinking_mode"],
        "reasoning_effort": runtime["reasoning_effort"],
        "tool_call_source": "native",
        "tool_call_count": 1,
        "tool_name": "probe_read_file",
        "tool_arguments_match": True,
        "round_trip_completed": True,
        "fallback_used": False,
        "error_code": "",
    }
    for key, value in expected.items():
        _refuse(preflight.get(key) == value, f"{candidate_id} preflight {key} drift")
    observed_model = str(preflight.get("observed_response_model") or "")
    _refuse(bool(observed_model), f"{candidate_id} preflight observed model is empty")
    _refuse(
        preflight.get("round_trip_observed_response_model") == observed_model,
        f"{candidate_id} round-trip observed model drift",
    )
    attempts_per_call = preflight.get("attempts_per_call")
    _refuse(
        isinstance(attempts_per_call, list)
        and len(attempts_per_call) == 2
        and all(
            isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 2
            for item in attempts_per_call
        ),
        f"{candidate_id} preflight attempts drift",
    )
    error_codes = preflight.get("error_codes")
    _refuse(
        isinstance(error_codes, list)
        and len(error_codes) <= sum(item - 1 for item in attempts_per_call)
        and all(str(item) in RETRYABLE_TRANSPORT_ERROR_CODES for item in error_codes),
        f"{candidate_id} preflight recorded a non-retryable provider error",
    )
    capability_source = str(preflight.get("capability_source") or "")
    _refuse(
        candidate["provider"] in capability_source
        and candidate["model"] in capability_source,
        f"{candidate_id} capability identity drift",
    )
    return CandidateIdentity(
        candidate_id=candidate_id,
        provider=candidate["provider"],
        model=candidate["model"],
        observed_model=observed_model,
        preflight_sha256=_sha256_file(path, f"{candidate_id} preflight"),
    )


def _candidate_output(metrics: CandidateMetrics) -> dict[str, Any]:
    patch_binding = _sha256_bytes(b"".join(metrics.patch_binding_parts or []))
    return {
        "candidate_id": metrics.candidate_id,
        "provider": metrics.provider,
        "requested_model": metrics.model,
        "observed_response_model": metrics.observed_model,
        "planned": metrics.planned,
        "finalized": metrics.finalized,
        "official_resolved": metrics.official_resolved,
        "official_unresolved": metrics.official_unresolved,
        "official_decided": metrics.official_decided,
        "empty_patches": metrics.empty_patches,
        "infrastructure_failures": metrics.infrastructure_failures,
        "failed_tool_calls": metrics.failed_tool_calls,
        "provider_tokens": metrics.provider_tokens,
        "estimated_cost_usd": float(metrics.estimated_cost_usd),
        "preflight_sha256": metrics.preflight_sha256,
        "patch_binding_sha256": patch_binding,
        "run_ids": metrics.run_ids,
        "shard_evidence": metrics.evidence,
    }


def _selection_key(
    candidate: dict[str, Any], candidate_order: list[str]
) -> tuple[Any, ...]:
    return (
        -int(candidate["official_resolved"]),
        -int(candidate["official_decided"]),
        int(candidate["empty_patches"]),
        int(candidate["infrastructure_failures"]),
        int(candidate["failed_tool_calls"]),
        candidate_order.index(str(candidate["candidate_id"])),
    )


def select_winner(
    candidates: list[dict[str, Any]], candidate_order: list[str]
) -> dict[str, Any]:
    """Apply the frozen lexicographic rule to two already-valid candidates."""

    _refuse(len(candidates) == 2, "winner selection requires two valid candidates")
    _refuse(
        {item["candidate_id"] for item in candidates} == set(candidate_order),
        "winner candidate set drift",
    )
    return min(candidates, key=lambda item: _selection_key(item, candidate_order))


def summarize(project_root: Path, artifact_root: Path) -> dict[str, Any]:
    """Validate every frozen artifact and return a deterministic safe summary."""

    project_root = project_root.resolve()
    artifact_root = artifact_root.resolve()
    protocol, command_manifest, case_ids, plans, hashes = _validate_preregistration(
        project_root, artifact_root
    )
    raw_candidates = protocol["candidates"]
    candidates = [
        {
            "candidate_id": str(item["candidate_id"]),
            "provider": str(item["provider"]),
            "model": str(item["model"]),
        }
        for item in raw_candidates
    ]
    candidate_order = list(protocol["execution"]["candidate_order"])
    first_plan = {
        candidate_id: next(plan for plan in plans if plan.candidate_id == candidate_id)
        for candidate_id in candidate_order
    }
    identities = {
        candidate["candidate_id"]: _validate_preflight(
            artifact_root,
            protocol,
            command_manifest,
            first_plan[candidate["candidate_id"]],
            candidate,
        )
        for candidate in candidates
    }
    metrics = {
        candidate["candidate_id"]: CandidateMetrics(
            candidate_id=candidate["candidate_id"],
            provider=candidate["provider"],
            model=candidate["model"],
            observed_model=identities[candidate["candidate_id"]].observed_model,
            preflight_sha256=identities[candidate["candidate_id"]].preflight_sha256,
        )
        for candidate in candidates
    }
    for plan in plans:
        skill_name, skill_version = protocol["fixed_runtime"]["skill"].rsplit("@", 1)
        bindings = (
            (str(PROTOCOL_PATH), hashes["protocol_sha256"]),
            (
                str(COMMAND_MANIFEST_PATH),
                hashes["command_manifest_sha256"],
            ),
            (
                str(protocol["development_set"]["manifest"]),
                hashes["development_set_manifest_sha256"],
            ),
            (
                str(PROBE_SCRIPT_PATH),
                str(command_manifest["capability_probe_script_sha256"]),
            ),
            (
                str(SUMMARIZER_SCRIPT_PATH),
                str(command_manifest["selection_summarizer_script_sha256"]),
            ),
            (
                str(artifact_root / "dataset-binding.json"),
                str(command_manifest["dataset_binding_sha256"]),
            ),
            (
                str(artifact_root / "dataset" / "agent-cases.json"),
                str(command_manifest["agent_dataset_sha256"]),
            ),
            (
                str(artifact_root / "dataset" / "official-cases.json"),
                str(command_manifest["official_dataset_sha256"]),
            ),
            (
                str(IMAGE_MANIFEST_PATH),
                str(command_manifest["image_manifest_sha256"]),
            ),
            (
                str(
                    Path("agent_forge")
                    / "skills"
                    / "packages"
                    / skill_name
                    / "SKILL.md"
                ),
                str(command_manifest["skill_file_sha256"]),
            ),
        )
        try:
            validated = validate_formal_run(
                FormalRunExpectation(
                    label=f"{plan.candidate_id}/{plan.shard}",
                    project_root=project_root,
                    artifact_root=artifact_root,
                    output_root=plan.output_root,
                    instance_ids=plan.instance_ids,
                    command_argv=plan.argv,
                    expected_source_identity=dict(command_manifest["source_identity"]),
                    expected_source_manifest_path=project_root / COMMAND_MANIFEST_PATH,
                    frozen_inputs=bindings,
                    observed_model=identities[plan.candidate_id].observed_model,
                    skill_name=skill_name,
                    skill_version=skill_version,
                    skill_content_sha256=str(command_manifest["skill_file_sha256"]),
                )
            )
        except SelectionRefused:
            raise
        target = metrics[plan.candidate_id]
        target.planned += validated.planned
        target.finalized += validated.finalized
        target.official_resolved += validated.resolved
        target.official_unresolved += validated.unresolved
        target.official_decided += validated.decided
        target.empty_patches += validated.empty
        target.infrastructure_failures += validated.infrastructure
        target.failed_tool_calls += validated.failed_tools
        target.provider_tokens += validated.tokens
        target.estimated_cost_usd += validated.cost
        assert target.run_ids is not None
        target.run_ids.append(validated.run_id)
        assert target.evidence is not None
        target.evidence.append(validated.evidence(plan.shard))
        assert target.patch_binding_parts is not None
        target.patch_binding_parts.extend(validated.patch_binding_parts)

    planned_per_candidate = protocol["development_set"]["planned_cases_per_candidate"]
    outputs: list[dict[str, Any]] = []
    for candidate_id in candidate_order:
        item = metrics[candidate_id]
        _refuse(
            item.planned == item.finalized == planned_per_candidate,
            f"{candidate_id} does not have the complete planned denominator",
        )
        _refuse(
            item.official_resolved + item.official_unresolved + item.empty_patches
            == planned_per_candidate,
            f"{candidate_id} official denominator is incomplete",
        )
        _refuse(
            item.infrastructure_failures == 0,
            f"{candidate_id} has infrastructure failures",
        )
        outputs.append(_candidate_output(item))

    winner = select_winner(outputs, candidate_order)
    # Re-hash the frozen inputs after all reads to fail if they changed mid-summary.
    _refuse(
        _sha256_file(project_root / PROTOCOL_PATH, "quality-selection protocol")
        == hashes["protocol_sha256"],
        "protocol changed while summarizing",
    )
    _refuse(
        _sha256_file(project_root / COMMAND_MANIFEST_PATH, "command manifest")
        == hashes["command_manifest_sha256"],
        "command manifest changed while summarizing",
    )
    return {
        "schema_version": 1,
        "selection_id": str(protocol["protocol_id"]),
        "status": "selected_after_both_candidates_validated",
        "development_set": {
            "role": protocol["development_set"]["role"],
            "planned_cases_per_candidate": planned_per_candidate,
            "planned_total_starts": protocol["development_set"]["planned_total_starts"],
            "ordered_case_ids_sha256": _ordered_ids_sha256(case_ids),
        },
        "frozen_inputs": hashes,
        "selection_rule": {
            "order": [
                "higher official_resolved",
                "higher official_decided",
                "fewer empty_patches",
                "fewer infrastructure_failures",
                "fewer failed_tool_calls",
                "candidate_order",
            ],
            "candidate_order": candidate_order,
            "fail_closed": (
                "Both candidates must be complete and valid; incomplete, infrastructure, "
                "identity, configuration, Skill, native-tool, fallback, or patch-byte drift "
                "produces no winner."
            ),
        },
        "candidates": outputs,
        "winner": {
            "candidate_id": winner["candidate_id"],
            "provider": winner["provider"],
            "requested_model": winner["requested_model"],
            "observed_response_model": winner["observed_response_model"],
            "selection_key": [
                winner["official_resolved"],
                winner["official_decided"],
                winner["empty_patches"],
                winner["infrastructure_failures"],
                winner["failed_tool_calls"],
                candidate_order.index(winner["candidate_id"]),
            ],
        },
        "claim_limits": list(protocol["claim_limits"]),
        "artifact_boundary": (
            "No sealed rows, gold patches, per-test logs, tests_status, or per-case "
            "official reports were read. Official outcomes come only from safe aggregate "
            "buckets; patch correctness is bound by exact candidate/prediction/evaluator bytes."
        ),
    }


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail-closed mechanical Golden-10 quality-profile selection."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    artifact_root = (
        args.artifact_root.resolve()
        if args.artifact_root
        else (project_root / DEFAULT_ARTIFACT_ROOT).resolve()
    )
    output = args.output or artifact_root / DEFAULT_OUTPUT_NAME
    try:
        summary = summarize(project_root, artifact_root)
        _write_json_atomic(output.resolve(), summary)
    except SelectionRefused as exc:
        print(f"quality-selection summary refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "status": summary["status"],
                "winner": summary["winner"]["candidate_id"],
                "output": str(output.resolve()),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
