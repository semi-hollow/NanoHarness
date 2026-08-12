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
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


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

COMMAND_VALUE_FLAGS = {
    "--agent-mode",
    "--base-url",
    "--cases-file",
    "--cost-budget-usd",
    "--dataset",
    "--execution-mode",
    "--instance-id",
    "--limit",
    "--max-context-chars",
    "--max-revision-rounds",
    "--max-steps",
    "--max-tool-calls-per-turn",
    "--max-prompt-tokens",
    "--max-workers",
    "--memory-recall-limit",
    "--model",
    "--model-request-timeout-seconds",
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
COMMAND_SWITCH_FLAGS = {"--evaluate", "--keep-worktree", "--no-keep-worktree"}

OFFICIAL_AGGREGATE_KEYS = {
    "schema_version",
    "submitted_ids",
    "completed_ids",
    "resolved_ids",
    "unresolved_ids",
    "error_ids",
    "empty_patch_ids",
    "incomplete_ids",
    "total_instances",
    "submitted_instances",
    "completed_instances",
    "resolved_instances",
    "unresolved_instances",
    "error_instances",
    "empty_patch_instances",
}
OFFICIAL_STATUS_FOR_BUCKET = {
    "resolved_ids": "official_resolved",
    "unresolved_ids": "official_eval_failed",
    "empty_patch_ids": "official_eval_skipped_empty_patch",
}
RETRYABLE_TRANSPORT_ERROR_CODES = {
    "request_failed",
    "request_timeout",
    "rate_limited",
    "server_error",
}


class SelectionRefused(RuntimeError):
    """The frozen experiment is incomplete, invalid, or has drifted."""


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


def _refuse(condition: bool, message: str) -> None:
    if not condition:
        raise SelectionRefused(message)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelectionRefused(f"cannot read {label}: {exc}") from exc
    _refuse(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path, label: str) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise SelectionRefused(f"cannot hash {label}: {exc}") from exc


def _as_int(value: Any, label: str) -> int:
    _refuse(
        isinstance(value, int) and not isinstance(value, bool),
        f"{label} must be an integer",
    )
    return value


def _as_decimal(value: Any, label: str) -> Decimal:
    _refuse(not isinstance(value, bool), f"{label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SelectionRefused(f"{label} must be numeric") from exc
    _refuse(parsed.is_finite(), f"{label} must be finite")
    return parsed


def _resolve_from_project(project_root: Path, raw_path: str, label: str) -> Path:
    _refuse(bool(raw_path), f"{label} path is empty")
    path = Path(raw_path)
    resolved = (path if path.is_absolute() else project_root / path).resolve()
    return resolved


def _assert_path(actual: Any, expected: Path, label: str) -> None:
    _refuse(isinstance(actual, str) and bool(actual), f"{label} path is missing")
    _refuse(Path(actual).resolve() == expected.resolve(), f"{label} path drift")


def _ordered_ids_sha256(case_ids: list[str]) -> str:
    return _sha256_bytes("\n".join(case_ids).encode("utf-8"))


def _safe_endpoint_identity(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    endpoint = f"{parsed.scheme}://{parsed.hostname or ''}"
    if parsed.port:
        endpoint += f":{parsed.port}"
    endpoint += parsed.path.rstrip("/")
    return endpoint, _sha256_bytes(base_url.encode("utf-8"))


def _parse_command(argv: Any, label: str) -> tuple[dict[str, str], list[str], set[str]]:
    _refuse(isinstance(argv, list), f"{label} argv must be a list")
    _refuse(
        argv[:3] == [".venv/bin/forge", "bench", "swebench"],
        f"{label} entrypoint drift",
    )
    values: dict[str, str] = {}
    instance_ids: list[str] = []
    switches: set[str] = set()
    index = 3
    while index < len(argv):
        flag = argv[index]
        _refuse(isinstance(flag, str), f"{label} contains a non-string token")
        if flag in COMMAND_SWITCH_FLAGS:
            _refuse(flag not in switches, f"{label} repeats {flag}")
            switches.add(flag)
            index += 1
            continue
        _refuse(flag in COMMAND_VALUE_FLAGS, f"{label} has unsupported flag {flag}")
        _refuse(index + 1 < len(argv), f"{label} is missing the value for {flag}")
        raw_value = argv[index + 1]
        _refuse(isinstance(raw_value, str), f"{label} has a non-string flag value")
        if flag == "--instance-id":
            instance_ids.append(raw_value)
        else:
            _refuse(flag not in values, f"{label} repeats {flag}")
            values[flag] = raw_value
        index += 2
    return values, instance_ids, switches


def _require_string_flag(
    values: dict[str, str], flag: str, expected: Any, label: str
) -> None:
    _refuse(values.get(flag) == str(expected), f"{label} {flag} drift")


def _require_numeric_flag(
    values: dict[str, str], flag: str, expected: Any, label: str
) -> None:
    _refuse(flag in values, f"{label} is missing {flag}")
    _refuse(
        _as_decimal(values[flag], f"{label} {flag}")
        == _as_decimal(expected, f"protocol {flag}"),
        f"{label} {flag} drift",
    )


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
        values, argv_ids, switches = _parse_command(command.get("argv"), label)
        _refuse(argv_ids == command_ids, f"{label} argv instance order drift")
        _refuse(len(command_ids) > 0, f"{label} has no cases")
        _refuse(
            switches == {"--evaluate", "--no-keep-worktree"},
            f"{label} switch set drift",
        )

        identity = candidate_by_id[candidate_id]
        _require_string_flag(values, "--provider", identity["provider"], label)
        _require_string_flag(values, "--model", identity["model"], label)
        _require_string_flag(values, "--agent-mode", runtime["agent_mode"], label)
        _require_string_flag(values, "--thinking", runtime["thinking_mode"], label)
        _require_string_flag(
            values, "--reasoning-effort", runtime["reasoning_effort"], label
        )
        _require_string_flag(
            values, "--tool-routing", runtime["tool_routing_mode"], label
        )
        _require_string_flag(values, "--skills", skill_name, label)
        _require_string_flag(
            values, "--execution-mode", runtime["execution_mode"], label
        )
        _require_string_flag(
            values, "--network-policy", runtime["network_policy"], label
        )
        _require_string_flag(
            values, "--official-namespace", runtime["official_namespace"], label
        )
        _require_string_flag(
            values,
            "--official-cache-level",
            runtime["official_cache_level"],
            label,
        )
        for flag, key in {
            "--temperature": "temperature",
            "--max-steps": "max_steps",
            "--max-context-chars": "max_context_chars",
            "--max-prompt-tokens": "max_prompt_tokens",
            "--reserved-output-tokens": "reserved_output_tokens",
            "--max-tool-calls-per-turn": "max_tool_calls_per_turn",
            "--timeout-seconds": "run_timeout_seconds_per_case",
            "--model-request-timeout-seconds": "model_request_timeout_seconds",
            "--tool-execution-timeout-seconds": "tool_execution_timeout_seconds",
            "--max-workers": "official_max_workers_per_shard",
            "--memory-recall-limit": "memory_recall_limit",
        }.items():
            _require_numeric_flag(values, flag, runtime[key], label)
        _require_numeric_flag(values, "--max-revision-rounds", 0, label)
        _require_numeric_flag(values, "--limit", len(command_ids), label)
        cost_budget = runtime.get("cost_budget_usd_per_case")
        if cost_budget is None:
            _refuse(
                "--cost-budget-usd" not in values,
                f"{label} unexpectedly enables a cost budget",
            )
        else:
            _require_numeric_flag(values, "--cost-budget-usd", cost_budget, label)

        base_url = values.get("--base-url", "")
        _refuse(bool(base_url), f"{label} base URL is missing")
        common_base_url = common_base_url or base_url
        _refuse(base_url == common_base_url, f"{label} base URL drift")

        dataset_path = _resolve_from_project(
            project_root, values.get("--dataset", ""), f"{label} dataset"
        )
        cases_path = _resolve_from_project(
            project_root, values.get("--cases-file", ""), f"{label} cases file"
        )
        expected_dataset = (artifact_root / "dataset" / "official-cases.json").resolve()
        expected_cases = (artifact_root / "dataset" / "agent-cases.json").resolve()
        _refuse(
            dataset_path == expected_dataset, f"{label} official dataset path drift"
        )
        _refuse(cases_path == expected_cases, f"{label} agent dataset path drift")
        _refuse(bool(values.get("--repo-cache")), f"{label} repo cache is missing")

        output_root = _resolve_from_project(
            project_root, values.get("--output-root", ""), f"{label} output root"
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


def _assert_result_config(
    results: dict[str, Any],
    scorecard: dict[str, Any],
    protocol: dict[str, Any],
    identity: CandidateIdentity,
    plan: CommandPlan,
    project_root: Path,
    artifact_root: Path,
    run_dir: Path,
) -> None:
    runtime = protocol["fixed_runtime"]
    golden = _read_json(
        _resolve_from_project(
            project_root,
            protocol["development_set"]["manifest"],
            "development-set manifest",
        ),
        "development-set manifest",
    )
    expected_dataset = (artifact_root / "dataset" / "official-cases.json").resolve()
    result_strings = {
        "provider": identity.provider,
        "model": identity.model,
        "split": str(golden.get("split") or ""),
        "thinking_mode": runtime["thinking_mode"],
        "reasoning_effort": runtime["reasoning_effort"],
        "agent_mode": runtime["agent_mode"],
        "tool_routing_mode": runtime["tool_routing_mode"],
        "execution_mode": runtime["execution_mode"],
        "network_policy": runtime["network_policy"],
        "official_namespace": runtime["official_namespace"],
    }
    for key, expected in result_strings.items():
        _refuse(
            results.get(key) == expected,
            f"{plan.candidate_id}/{plan.shard} {key} drift",
        )
    result_numbers = {
        "temperature": runtime["temperature"],
        "max_steps": runtime["max_steps"],
        "max_context_chars": runtime["max_context_chars"],
        "max_prompt_tokens": runtime["max_prompt_tokens"],
        "reserved_output_tokens": runtime["reserved_output_tokens"],
        "max_tool_calls_per_turn": runtime["max_tool_calls_per_turn"],
        "timeout_seconds": runtime["run_timeout_seconds_per_case"],
        "model_request_timeout_seconds": runtime["model_request_timeout_seconds"],
        "tool_execution_timeout_seconds": runtime["tool_execution_timeout_seconds"],
        "memory_recall_limit": runtime["memory_recall_limit"],
        "max_revision_rounds": 0,
    }
    for key, expected in result_numbers.items():
        _refuse(
            _as_decimal(results.get(key), f"results {key}")
            == _as_decimal(expected, f"protocol {key}"),
            f"{plan.candidate_id}/{plan.shard} {key} drift",
        )
    _refuse(
        results.get("cost_budget_usd") == runtime.get("cost_budget_usd_per_case"),
        f"{plan.candidate_id}/{plan.shard} cost budget drift",
    )
    _refuse(
        results.get("keep_worktree") is runtime["keep_worktree"],
        f"{plan.candidate_id}/{plan.shard} worktree retention drift",
    )
    skill_name, _ = runtime["skill"].rsplit("@", 1)
    _refuse(results.get("skill_mode") == "auto", "formal skill mode drift")
    _refuse(results.get("skill_names") == [skill_name], "formal skill name drift")
    _refuse(
        results.get("skill_manifest_sha256") == "builtins_only",
        "formal built-in skill source drift",
    )
    _refuse(
        results.get("memory_namespace") == "swebench:<instance_id>",
        "formal memory namespace drift",
    )
    _assert_path(results.get("dataset_name"), expected_dataset, "results dataset")
    _assert_path(results.get("output_dir"), run_dir, "results output_dir")
    _assert_path(
        results.get("predictions_path"),
        run_dir / "predictions.jsonl",
        "results predictions",
    )

    metadata = scorecard.get("metadata")
    _refuse(isinstance(metadata, dict), "scorecard metadata is missing")
    metadata_expected = {
        "provider": identity.provider,
        "requested_model": identity.model,
        "observed_models": [identity.observed_model],
        "split": str(golden.get("split") or ""),
        "thinking_mode": runtime["thinking_mode"],
        "reasoning_effort": runtime["reasoning_effort"],
        "agent_mode": runtime["agent_mode"],
        "max_steps": runtime["max_steps"],
        "max_context_chars": runtime["max_context_chars"],
        "max_prompt_tokens": runtime["max_prompt_tokens"],
        "reserved_output_tokens": runtime["reserved_output_tokens"],
        "max_tool_calls_per_turn": runtime["max_tool_calls_per_turn"],
        "cost_budget_usd": runtime.get("cost_budget_usd_per_case"),
        "timeout_seconds": runtime["run_timeout_seconds_per_case"],
        "model_request_timeout_seconds": runtime["model_request_timeout_seconds"],
        "tool_execution_timeout_seconds": runtime["tool_execution_timeout_seconds"],
        "max_revision_rounds": 0,
        "tool_routing_mode": runtime["tool_routing_mode"],
        "skill_mode": "auto",
        "skill_names": [skill_name],
        "skill_manifest_sha256": "builtins_only",
        "memory_recall_limit": runtime["memory_recall_limit"],
        "execution_mode": runtime["execution_mode"],
        "network_policy": runtime["network_policy"],
        "keep_worktree": runtime["keep_worktree"],
        "official_namespace": runtime["official_namespace"],
    }
    for key, expected in metadata_expected.items():
        _refuse(
            metadata.get(key) == expected,
            f"{plan.candidate_id}/{plan.shard} scorecard {key} drift",
        )
    _assert_path(metadata.get("dataset_name"), expected_dataset, "scorecard dataset")


def _read_predictions(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SelectionRefused(f"cannot read {label}: {exc}") from exc
    predictions: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        _refuse(bool(line.strip()), f"{label} contains an empty line")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SelectionRefused(f"{label}[{index}] is invalid JSON") from exc
        _refuse(isinstance(value, dict), f"{label}[{index}] must be an object")
        predictions.append(value)
    return predictions


def _safe_official_aggregate(path: Path, label: str) -> dict[str, Any]:
    aggregate = _read_json(path, label)
    _refuse(
        set(aggregate) == OFFICIAL_AGGREGATE_KEYS,
        f"{label} is not the safe official run aggregate",
    )
    _refuse("tests_status" not in aggregate, f"{label} contains per-test data")
    return aggregate


def _list_of_ids(aggregate: dict[str, Any], key: str, label: str) -> list[str]:
    value = aggregate.get(key)
    _refuse(isinstance(value, list), f"{label} {key} must be a list")
    result = [str(item) for item in value]
    _refuse(len(result) == len(set(result)), f"{label} {key} contains duplicates")
    return result


def _validate_official_aggregate(
    aggregate: dict[str, Any],
    instance_ids: tuple[str, ...],
    label: str,
) -> dict[str, list[str]]:
    expected = set(instance_ids)
    buckets = {
        key: _list_of_ids(aggregate, key, label)
        for key in (
            "resolved_ids",
            "unresolved_ids",
            "error_ids",
            "empty_patch_ids",
            "incomplete_ids",
        )
    }
    seen: set[str] = set()
    for key, values in buckets.items():
        _refuse(set(values) <= expected, f"{label} {key} contains an unplanned id")
        _refuse(seen.isdisjoint(values), f"{label} official outcome buckets overlap")
        seen.update(values)
    _refuse(seen == expected, f"{label} official outcome partition is incomplete")
    _refuse(not buckets["error_ids"], f"{label} has evaluator infrastructure errors")
    _refuse(not buckets["incomplete_ids"], f"{label} has incomplete evaluation")

    submitted = _list_of_ids(aggregate, "submitted_ids", label)
    completed = _list_of_ids(aggregate, "completed_ids", label)
    decided = buckets["resolved_ids"] + buckets["unresolved_ids"]
    _refuse(set(submitted) == expected, f"{label} submitted denominator drift")
    _refuse(set(completed) == set(decided), f"{label} completed ids drift")
    count_expectations = {
        "total_instances": len(instance_ids),
        "submitted_instances": len(instance_ids),
        "completed_instances": len(decided),
        "resolved_instances": len(buckets["resolved_ids"]),
        "unresolved_instances": len(buckets["unresolved_ids"]),
        "error_instances": 0,
        "empty_patch_instances": len(buckets["empty_patch_ids"]),
    }
    for key, expected_count in count_expectations.items():
        _refuse(aggregate.get(key) == expected_count, f"{label} {key} drift")
    _refuse(aggregate.get("schema_version") == 2, f"{label} schema drift")
    return buckets


def _trace_metadata(
    trace_path: Path,
    usage_path: Path,
    identity: CandidateIdentity,
    skill_name: str,
    skill_version: str,
    skill_sha256: str,
    label: str,
) -> tuple[int, Decimal, int, int]:
    trace = _read_json(trace_path, f"{label} trace")
    events = trace.get("events")
    _refuse(isinstance(events, list), f"{label} trace events are missing")
    capabilities: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    trace_calls: list[dict[str, Any]] = []
    for event in events:
        _refuse(isinstance(event, dict), f"{label} trace event must be an object")
        event_type = event.get("event_type")
        if event_type == "model_capabilities":
            capabilities.append(event)
        elif event_type == "skill_selection":
            skills.append(event)
        elif event_type == "llm_call":
            model_usage = event.get("model_usage")
            _refuse(isinstance(model_usage, dict), f"{label} model usage is missing")
            _refuse(
                model_usage.get("provider") == identity.provider,
                f"{label} formal provider drift",
            )
            _refuse(
                model_usage.get("model") == identity.model,
                f"{label} requested model drift",
            )
            _refuse(
                model_usage.get("observed_models") == [identity.observed_model],
                f"{label} provider-reported model drift",
            )
            _refuse(
                model_usage.get("fallback_used") is False,
                f"{label} used a fallback model",
            )
            _refuse(
                model_usage.get("fallback_provider") in {None, ""}
                and model_usage.get("fallback_model") in {None, ""},
                f"{label} records a fallback identity",
            )
            attempts = _as_int(model_usage.get("attempts"), f"{label} attempts")
            _refuse(1 <= attempts <= 2, f"{label} provider attempts drift")
            error_codes_value = model_usage.get("error_codes")
            _refuse(
                isinstance(error_codes_value, list)
                and len(error_codes_value) <= attempts - 1
                and all(
                    str(item) in RETRYABLE_TRANSPORT_ERROR_CODES
                    for item in error_codes_value
                ),
                f"{label} has non-retryable provider errors",
            )
            error_codes = [str(item) for item in error_codes_value]
            usage_source = str(model_usage.get("usage_source") or "")
            _refuse(
                usage_source in {"provider", "estimate"},
                f"{label} usage source drift",
            )
            tokens = _as_int(model_usage.get("total_tokens"), f"{label} tokens")
            _refuse(tokens >= 0, f"{label} token usage is negative")
            cost = _as_decimal(model_usage.get("estimated_cost_usd"), f"{label} cost")
            _refuse(cost >= 0, f"{label} cost is negative")
            tool_count = _as_int(event.get("tool_call_count"), f"{label} tool count")
            normalization = event.get("response_normalization")
            _refuse(
                isinstance(normalization, dict), f"{label} normalization is missing"
            )
            tool_source = str(normalization.get("tool_call_source") or "")
            if tool_count:
                _refuse(tool_source == "native", f"{label} used non-native tool calls")
            trace_calls.append(
                {
                    "provider": identity.provider,
                    "model": identity.model,
                    "provider_reported_models": [identity.observed_model],
                    "fallback_used": False,
                    "fallback_provider": str(
                        model_usage.get("fallback_provider") or ""
                    ),
                    "fallback_model": str(model_usage.get("fallback_model") or ""),
                    "error_codes": error_codes,
                    "attempts": attempts,
                    "usage_source": usage_source,
                    "total_tokens": tokens,
                    "estimated_cost_usd": cost,
                    "tool_call_source": tool_source,
                }
            )
    _refuse(len(capabilities) == 1, f"{label} capability metadata drift")
    capability = capabilities[0].get("model_capabilities")
    _refuse(isinstance(capability, dict), f"{label} capability payload is missing")
    _refuse(
        capability.get("native_tool_calling") is True, f"{label} native tools disabled"
    )
    capability_source = str(capability.get("source") or "")
    _refuse(
        identity.provider in capability_source and identity.model in capability_source,
        f"{label} capability source drift",
    )
    _refuse(len(skills) == 1, f"{label} skill selection metadata drift")
    skill_event = skills[0]
    _refuse(skill_event.get("skill_mode") == "auto", f"{label} skill mode drift")
    selected_skills = skill_event.get("skills")
    _refuse(isinstance(selected_skills, list), f"{label} selected skills are missing")
    _refuse(len(selected_skills) == 1, f"{label} selected skill count drift")
    selected_skill = selected_skills[0]
    _refuse(isinstance(selected_skill, dict), f"{label} selected skill is invalid")
    _refuse(
        {
            "name": selected_skill.get("name"),
            "version": selected_skill.get("version"),
            "content_sha256": selected_skill.get("content_sha256"),
        }
        == {
            "name": skill_name,
            "version": skill_version,
            "content_sha256": skill_sha256,
        },
        f"{label} Skill identity drift",
    )
    _refuse(trace_calls, f"{label} made no successful provider calls")

    usage = _read_json(usage_path, f"{label} usage")
    steps = usage.get("steps")
    _refuse(isinstance(steps, list), f"{label} usage steps are missing")
    usage_calls: list[dict[str, Any]] = []
    for step in steps:
        _refuse(isinstance(step, dict), f"{label} usage step is invalid")
        calls = step.get("llm_calls")
        _refuse(isinstance(calls, list), f"{label} usage calls are missing")
        for call in calls:
            _refuse(isinstance(call, dict), f"{label} usage call is invalid")
            normalization = call.get("response_normalization") or {}
            usage_calls.append(
                {
                    "provider": call.get("provider"),
                    "model": call.get("model"),
                    "provider_reported_models": call.get("provider_reported_models"),
                    "fallback_used": call.get("fallback_used"),
                    "fallback_provider": str(call.get("fallback_provider") or ""),
                    "fallback_model": str(call.get("fallback_model") or ""),
                    "error_codes": call.get("error_codes"),
                    "attempts": call.get("attempts"),
                    "usage_source": call.get("usage_source"),
                    "total_tokens": call.get("total_tokens"),
                    "estimated_cost_usd": _as_decimal(
                        call.get("estimated_cost_usd"), f"{label} usage cost"
                    ),
                    "tool_call_source": normalization.get("tool_call_source"),
                }
            )
    _refuse(usage_calls == trace_calls, f"{label} trace/usage call metadata drift")
    summary = usage.get("summary")
    _refuse(isinstance(summary, dict), f"{label} usage summary is missing")
    total_tokens = sum(item["total_tokens"] for item in trace_calls)
    total_cost = sum((item["estimated_cost_usd"] for item in trace_calls), Decimal("0"))
    _refuse(summary.get("llm_calls") == len(trace_calls), f"{label} LLM count drift")
    _refuse(summary.get("total_tokens") == total_tokens, f"{label} token total drift")
    _refuse(
        _as_decimal(summary.get("estimated_cost_usd"), f"{label} summary cost")
        == total_cost,
        f"{label} cost total drift",
    )
    _refuse(
        summary.get("active_skills") == [skill_name],
        f"{label} active Skill drift",
    )
    failed_tools = _as_int(summary.get("failed_tool_calls"), f"{label} failed tools")
    _refuse(failed_tools >= 0, f"{label} failed tool count is negative")
    return total_tokens, total_cost, failed_tools, len(trace_calls)


def _validate_run(
    project_root: Path,
    artifact_root: Path,
    protocol: dict[str, Any],
    command_manifest: dict[str, Any],
    plan: CommandPlan,
    identity: CandidateIdentity,
) -> dict[str, Any]:
    _refuse(
        plan.output_root.is_dir(), f"{plan.candidate_id}/{plan.shard} output is missing"
    )
    child_dirs = sorted(path for path in plan.output_root.iterdir() if path.is_dir())
    _refuse(
        len(child_dirs) == 1,
        f"{plan.candidate_id}/{plan.shard} must contain exactly one formal run",
    )
    run_dir = child_dirs[0].resolve()
    results_path = run_dir / "results.json"
    scorecard_path = run_dir / "scorecard.json"
    predictions_path = run_dir / "predictions.jsonl"
    results = _read_json(results_path, f"{plan.candidate_id}/{plan.shard} results")
    scorecard = _read_json(
        scorecard_path, f"{plan.candidate_id}/{plan.shard} scorecard"
    )
    _assert_result_config(
        results,
        scorecard,
        protocol,
        identity,
        plan,
        project_root,
        artifact_root,
        run_dir,
    )
    run_id = str(results.get("run_id") or "")
    _refuse(bool(run_id), f"{plan.candidate_id}/{plan.shard} run id is missing")
    _refuse(run_dir.name == run_id, f"{plan.candidate_id}/{plan.shard} run id drift")
    case_results = results.get("case_results")
    _refuse(isinstance(case_results, list), "results case_results are missing")
    _refuse(
        [item.get("instance_id") for item in case_results if isinstance(item, dict)]
        == list(plan.instance_ids),
        f"{plan.candidate_id}/{plan.shard} finalized case order drift",
    )
    _refuse(
        len(case_results) == len(plan.instance_ids),
        f"{plan.candidate_id}/{plan.shard} planned denominator is incomplete",
    )
    predictions = _read_predictions(
        predictions_path, f"{plan.candidate_id}/{plan.shard} predictions"
    )
    _refuse(
        [item.get("instance_id") for item in predictions] == list(plan.instance_ids),
        f"{plan.candidate_id}/{plan.shard} prediction order drift",
    )
    prediction_name = f"agent-forge-{identity.provider}-{identity.model}"
    _refuse(
        all(item.get("model_name_or_path") == prediction_name for item in predictions),
        f"{plan.candidate_id}/{plan.shard} prediction model identity drift",
    )

    _refuse(results.get("official_eval_exit_code") == 0, "official evaluator failed")
    _refuse(results.get("official_eval_warnings") == [], "official evaluator warned")
    official_command = results.get("official_eval_command")
    _refuse(isinstance(official_command, list), "official evaluator command is missing")
    expected_official_tail = [
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        str((artifact_root / "dataset" / "official-cases.json").resolve()),
        "--split",
        str(
            _read_json(
                _resolve_from_project(
                    project_root,
                    protocol["development_set"]["manifest"],
                    "development-set manifest",
                ),
                "development-set manifest",
            ).get("split")
            or ""
        ),
        "--predictions_path",
        str(predictions_path.resolve()),
        "--max_workers",
        str(protocol["fixed_runtime"]["official_max_workers_per_shard"]),
        "--cache_level",
        str(protocol["fixed_runtime"]["official_cache_level"]),
        "--run_id",
        run_id,
        "--instance_ids",
        *plan.instance_ids,
        "--namespace",
        str(protocol["fixed_runtime"]["official_namespace"]),
    ]
    _refuse(
        len(official_command) > 1 and official_command[1:] == expected_official_tail,
        f"{plan.candidate_id}/{plan.shard} official command drift",
    )

    official_path_value = results.get("official_eval_report_path")
    _refuse(isinstance(official_path_value, str), "official aggregate path is missing")
    official_path = Path(official_path_value).resolve()
    expected_official_path = run_dir / f"{prediction_name}.{run_id}.json"
    _refuse(
        official_path == expected_official_path.resolve(),
        f"{plan.candidate_id}/{plan.shard} official aggregate path drift",
    )
    aggregate = _safe_official_aggregate(
        official_path, f"{plan.candidate_id}/{plan.shard} official aggregate"
    )
    buckets = _validate_official_aggregate(
        aggregate,
        plan.instance_ids,
        f"{plan.candidate_id}/{plan.shard}",
    )

    scorecard_cases = scorecard.get("cases")
    _refuse(isinstance(scorecard_cases, list), "scorecard cases are missing")
    _refuse(
        [item.get("instance_id") for item in scorecard_cases if isinstance(item, dict)]
        == list(plan.instance_ids),
        f"{plan.candidate_id}/{plan.shard} scorecard case order drift",
    )
    scorecard_by_id = {str(item["instance_id"]): item for item in scorecard_cases}
    case_result_by_id = {str(item["instance_id"]): item for item in case_results}
    prediction_by_id = {str(item["instance_id"]): item for item in predictions}
    skill_name, skill_version = protocol["fixed_runtime"]["skill"].rsplit("@", 1)
    skill_sha256 = str(command_manifest["skill_file_sha256"])
    total_tokens = 0
    total_cost = Decimal("0")
    failed_tools = 0
    patch_count = 0
    patch_binding_parts: list[bytes] = []

    bucket_for_id = {
        instance_id: key for key, values in buckets.items() for instance_id in values
    }
    for instance_id in plan.instance_ids:
        label = f"{plan.candidate_id}/{plan.shard}/{instance_id}"
        result = case_result_by_id[instance_id]
        _refuse(isinstance(result, dict), f"{label} result is invalid")
        _refuse(not result.get("error"), f"{label} has a runner/provider error")
        case_dir = run_dir / "cases" / instance_id
        candidate_path = case_dir / "candidate_changes.diff"
        trace_path = case_dir / "trace.json"
        usage_report_path = case_dir / "usage_report.md"
        usage_path = case_dir / "usage.json"
        _assert_path(
            result.get("candidate_diff_path"), candidate_path, f"{label} candidate"
        )
        _assert_path(result.get("trace_path"), trace_path, f"{label} trace")
        _assert_path(
            result.get("usage_report_path"), usage_report_path, f"{label} usage"
        )
        _refuse(usage_report_path.is_file(), f"{label} usage report is missing")
        try:
            candidate_bytes = candidate_path.read_bytes()
            candidate_text = candidate_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise SelectionRefused(
                f"cannot read {label} candidate patch: {exc}"
            ) from exc
        prediction_patch = prediction_by_id[instance_id].get("model_patch")
        _refuse(
            isinstance(prediction_patch, str), f"{label} prediction patch is invalid"
        )
        prediction_bytes = prediction_patch.encode("utf-8")
        _refuse(
            candidate_bytes == prediction_bytes, f"{label} candidate/prediction drift"
        )
        _refuse(
            result.get("patch_chars") == len(candidate_text),
            f"{label} patch size drift",
        )
        is_empty = not candidate_bytes
        bucket = bucket_for_id[instance_id]
        if is_empty:
            _refuse(bucket == "empty_patch_ids", f"{label} empty-patch outcome drift")
        else:
            _refuse(
                bucket in {"resolved_ids", "unresolved_ids"},
                f"{label} non-empty patch lacks an official decision",
            )
            official_patch = (
                run_dir
                / "logs"
                / "run_evaluation"
                / run_id
                / prediction_name
                / instance_id
                / "patch.diff"
            )
            try:
                official_bytes = official_patch.read_bytes()
            except OSError as exc:
                raise SelectionRefused(
                    f"cannot read {label} official patch: {exc}"
                ) from exc
            _refuse(
                candidate_bytes == official_bytes, f"{label} official patch-byte drift"
            )
            patch_count += 1
        expected_official_status = OFFICIAL_STATUS_FOR_BUCKET[bucket]
        _refuse(
            result.get("official_evaluation_status") == expected_official_status,
            f"{label} result/official outcome drift",
        )
        case_score = scorecard_by_id[instance_id]
        _refuse(isinstance(case_score, dict), f"{label} scorecard case is invalid")
        tokens, cost, case_failed_tools, _ = _trace_metadata(
            trace_path,
            usage_path,
            identity,
            skill_name,
            skill_version,
            skill_sha256,
            label,
        )
        score_expectations = {
            "patch_chars": len(candidate_text),
            "patch_generated": not is_empty,
            "official_evaluation_status": expected_official_status,
            "official_evaluated": bucket in {"resolved_ids", "unresolved_ids"},
            "official_resolved": bucket == "resolved_ids",
            "total_tokens": tokens,
            "failed_tool_calls": case_failed_tools,
        }
        for key, expected in score_expectations.items():
            _refuse(case_score.get(key) == expected, f"{label} scorecard {key} drift")
        _refuse(
            _as_decimal(case_score.get("estimated_cost_usd"), f"{label} score cost")
            == cost,
            f"{label} scorecard cost drift",
        )
        total_tokens += tokens
        total_cost += cost
        failed_tools += case_failed_tools
        patch_binding_parts.extend(
            [instance_id.encode("utf-8"), b"\0", candidate_bytes, b"\0"]
        )

    metrics = scorecard.get("metrics")
    _refuse(isinstance(metrics, dict), "scorecard metrics are missing")
    metric_expectations = {
        "case_count": len(plan.instance_ids),
        "patch_generated_count": patch_count,
        "official_evaluated_count": len(buckets["resolved_ids"])
        + len(buckets["unresolved_ids"]),
        "official_resolved_count": len(buckets["resolved_ids"]),
        "total_tokens": total_tokens,
        "failed_tool_calls": failed_tools,
    }
    for key, expected in metric_expectations.items():
        _refuse(metrics.get(key) == expected, f"scorecard metric {key} drift")
    _refuse(
        _as_decimal(metrics.get("estimated_cost_usd"), "scorecard total cost")
        == total_cost,
        "scorecard total cost drift",
    )
    return {
        "run_id": run_id,
        "planned": len(plan.instance_ids),
        "finalized": len(case_results),
        "resolved": len(buckets["resolved_ids"]),
        "unresolved": len(buckets["unresolved_ids"]),
        "decided": len(buckets["resolved_ids"]) + len(buckets["unresolved_ids"]),
        "empty": len(buckets["empty_patch_ids"]),
        "infrastructure": len(buckets["error_ids"]) + len(buckets["incomplete_ids"]),
        "failed_tools": failed_tools,
        "tokens": total_tokens,
        "cost": total_cost,
        "patch_binding_parts": patch_binding_parts,
        "evidence": {
            "shard": plan.shard,
            "run_id": run_id,
            "results_sha256": _sha256_file(results_path, f"{plan.shard} results"),
            "scorecard_sha256": _sha256_file(scorecard_path, f"{plan.shard} scorecard"),
            "predictions_sha256": _sha256_file(
                predictions_path, f"{plan.shard} predictions"
            ),
            "official_aggregate_sha256": _sha256_file(
                official_path, f"{plan.shard} official aggregate"
            ),
        },
    }


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
        result = _validate_run(
            project_root,
            artifact_root,
            protocol,
            command_manifest,
            plan,
            identities[plan.candidate_id],
        )
        target = metrics[plan.candidate_id]
        target.planned += result["planned"]
        target.finalized += result["finalized"]
        target.official_resolved += result["resolved"]
        target.official_unresolved += result["unresolved"]
        target.official_decided += result["decided"]
        target.empty_patches += result["empty"]
        target.infrastructure_failures += result["infrastructure"]
        target.failed_tool_calls += result["failed_tools"]
        target.provider_tokens += result["tokens"]
        target.estimated_cost_usd += result["cost"]
        target.run_ids.append(result["run_id"])
        target.evidence.append(result["evidence"])
        target.patch_binding_parts.extend(result["patch_binding_parts"])

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
