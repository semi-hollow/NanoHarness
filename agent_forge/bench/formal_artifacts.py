"""冻结 benchmark run 产物的 fail-closed 验证器。

验证器只消费安全的聚合证据，不打开密封数据行、gold patch 或 official
per-test report。
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from argparse import _SubParsersAction
from contextlib import redirect_stderr
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence, cast


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
OFFICIAL_PATCH_FILENAME = "patch" + ".diff"


class FormalArtifactRefused(RuntimeError):
    """冻结 run 不完整、不一致或已无法复现。"""


@dataclass(frozen=True)
class FormalRunExpectation:
    """用于校验一次 formal run 的冻结预期。

    ``expected_source_*`` 只认证 manifest/config 预期，不能表示 run-source
    provenance；后者必须由 lifecycle 在进程启动前写入 launch ledger。
    """

    label: str
    project_root: Path
    artifact_root: Path
    output_root: Path
    instance_ids: tuple[str, ...]
    command_argv: tuple[str, ...]
    expected_source_identity: Mapping[str, Any]
    expected_source_manifest_path: Path
    frozen_inputs: tuple[tuple[str, str], ...]
    observed_model: str
    skill_name: str
    skill_version: str
    skill_content_sha256: str
    max_transport_attempts: int = 2
    allowed_transport_error_codes: frozenset[str] = frozenset(
        RETRYABLE_TRANSPORT_ERROR_CODES
    )


@dataclass(frozen=True)
class ValidatedFormalRun:
    """一次完整通过校验的 formal run 的类型化、hash-bound 证据。"""

    run_id: str
    planned: int
    finalized: int
    resolved: int
    unresolved: int
    decided: int
    empty: int
    infrastructure: int
    failed_tools: int
    tokens: int
    cost: Decimal
    patch_binding_parts: tuple[bytes, ...]
    artifact_sha256: Mapping[str, str]
    command_argv_sha256: str
    expected_source_identity_sha256: str
    frozen_inputs_sha256: str
    config_sha256: str
    transport_retries: int = 0

    def evidence(self, shard: str) -> dict[str, Any]:
        return {
            "shard": shard,
            "run_id": self.run_id,
            "results_sha256": self.artifact_sha256["results.json"],
            "scorecard_sha256": self.artifact_sha256["scorecard.json"],
            "predictions_sha256": self.artifact_sha256["predictions.jsonl"],
            "official_aggregate_sha256": self.artifact_sha256[
                "official_aggregate.json"
            ],
            "artifact_bundle_sha256": _json_sha256(self.artifact_sha256),
            "command_argv_sha256": self.command_argv_sha256,
            "expected_source_identity_sha256": self.expected_source_identity_sha256,
            "frozen_inputs_sha256": self.frozen_inputs_sha256,
            "config_sha256": self.config_sha256,
            "transport_retries": self.transport_retries,
        }


def validate_formal_run(expectation: FormalRunExpectation) -> ValidatedFormalRun:
    """校验配置、模型身份、official outcome 与每个 patch byte。"""

    root = expectation.project_root.resolve()
    artifact_root = expectation.artifact_root.resolve()
    output_root = expectation.output_root.resolve()
    _assert_within(artifact_root, root / ".agent_forge", "formal artifact root")
    _assert_within(output_root, artifact_root, f"{expectation.label} output")
    _refuse(output_root.is_dir(), f"{expectation.label} output is missing")
    command = parse_formal_cli(expectation.command_argv, expectation.label)
    _validate_command_identity(command, expectation)
    command_sha256 = _json_sha256(list(expectation.command_argv))
    source_sha256 = _validate_expected_source_identity(expectation, root)
    input_hashes = _validate_frozen_inputs(expectation, root)
    frozen_inputs_sha256 = _json_sha256(input_hashes)
    config_sha256 = _json_sha256(
        {
            "command_argv_sha256": command_sha256,
            "parsed_cli": command,
            "expected_source_identity_sha256": source_sha256,
            "frozen_inputs_sha256": frozen_inputs_sha256,
            "observed_model": expectation.observed_model,
            "skill": {
                "name": expectation.skill_name,
                "version": expectation.skill_version,
                "content_sha256": expectation.skill_content_sha256,
            },
            "transport_policy": {
                "max_attempts": expectation.max_transport_attempts,
                "allowed_error_codes": sorted(
                    expectation.allowed_transport_error_codes
                ),
            },
        }
    )

    child_dirs = sorted(path for path in output_root.iterdir() if path.is_dir())
    _refuse(
        len(child_dirs) == 1,
        f"{expectation.label} must contain exactly one formal run",
    )
    run_dir = child_dirs[0].resolve()
    _assert_within(run_dir, output_root, f"{expectation.label} run")
    results_path = run_dir / "results.json"
    scorecard_path = run_dir / "scorecard.json"
    predictions_path = run_dir / "predictions.jsonl"
    for path in (results_path, scorecard_path, predictions_path):
        _assert_within(path.resolve(), run_dir, f"{expectation.label} artifact")
    results = _read_json(results_path, f"{expectation.label} results")
    scorecard = _read_json(scorecard_path, f"{expectation.label} scorecard")
    _assert_result_config(
        results,
        scorecard,
        expectation,
        command,
        run_dir,
        predictions_path,
    )
    run_id = str(results.get("run_id") or "")
    _refuse(bool(run_id), f"{expectation.label} run id is missing")
    _refuse(run_dir.name == run_id, f"{expectation.label} run id drift")
    case_results = results.get("case_results")
    _refuse(isinstance(case_results, list), "results case_results are missing")
    case_results = cast(list[Any], case_results)
    _refuse(
        [item.get("instance_id") for item in case_results if isinstance(item, dict)]
        == list(expectation.instance_ids),
        f"{expectation.label} finalized case order drift",
    )
    _refuse(
        len(case_results) == len(expectation.instance_ids),
        f"{expectation.label} planned denominator is incomplete",
    )
    predictions = _read_predictions(
        predictions_path, f"{expectation.label} predictions"
    )
    _refuse(
        [item.get("instance_id") for item in predictions]
        == list(expectation.instance_ids),
        f"{expectation.label} prediction order drift",
    )
    prediction_name = f"agent-forge-{command['provider']}-{command['model']}"
    _refuse(
        all(item.get("model_name_or_path") == prediction_name for item in predictions),
        f"{expectation.label} prediction model identity drift",
    )

    _refuse(results.get("official_eval_exit_code") == 0, "official evaluator failed")
    _refuse(results.get("official_eval_warnings") == [], "official evaluator warned")
    official_command = results.get("official_eval_command")
    _refuse(isinstance(official_command, list), "official evaluator command is missing")
    official_command = cast(list[Any], official_command)
    expected_official_tail = [
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        str(_resolve_cli_path(root, str(command["dataset"]))),
        "--split",
        str(command["split"]),
        "--predictions_path",
        str(predictions_path.resolve()),
        "--max_workers",
        str(command["max_workers"]),
        "--cache_level",
        str(command["official_cache_level"]),
        "--run_id",
        run_id,
        "--instance_ids",
        *expectation.instance_ids,
        "--namespace",
        "" if command["namespace_empty"] else str(command["official_namespace"]),
    ]
    _refuse(
        len(official_command) > 1 and official_command[1:] == expected_official_tail,
        f"{expectation.label} official command drift",
    )
    official_value = results.get("official_eval_report_path")
    _refuse(isinstance(official_value, str), "official aggregate path is missing")
    official_value = cast(str, official_value)
    official_path = Path(official_value).resolve()
    expected_official_path = run_dir / f"{prediction_name}.{run_id}.json"
    _assert_within(official_path, run_dir, f"{expectation.label} official aggregate")
    _refuse(
        official_path == expected_official_path.resolve(),
        f"{expectation.label} official aggregate path drift",
    )
    aggregate = _safe_official_aggregate(
        official_path, f"{expectation.label} official aggregate"
    )
    buckets = _validate_official_aggregate(
        aggregate, expectation.instance_ids, expectation.label
    )

    scorecard_cases = scorecard.get("cases")
    _refuse(isinstance(scorecard_cases, list), "scorecard cases are missing")
    scorecard_cases = cast(list[dict[str, Any]], scorecard_cases)
    _refuse(
        [item.get("instance_id") for item in scorecard_cases if isinstance(item, dict)]
        == list(expectation.instance_ids),
        f"{expectation.label} scorecard case order drift",
    )
    score_by_id = {str(item["instance_id"]): item for item in scorecard_cases}
    result_by_id = {str(item["instance_id"]): item for item in case_results}
    prediction_by_id = {str(item["instance_id"]): item for item in predictions}
    bucket_for_id = {
        instance_id: key for key, values in buckets.items() for instance_id in values
    }
    total_tokens = 0
    total_cost = Decimal("0")
    failed_tools = 0
    transport_retries = 0
    patch_count = 0
    patch_binding_parts: list[bytes] = []
    artifact_hashes = {
        "results.json": _sha256_file(results_path, "results"),
        "scorecard.json": _sha256_file(scorecard_path, "scorecard"),
        "predictions.jsonl": _sha256_file(predictions_path, "predictions"),
        "official_aggregate.json": _sha256_file(official_path, "official aggregate"),
    }

    for instance_id in expectation.instance_ids:
        label = f"{expectation.label}/{instance_id}"
        result = result_by_id[instance_id]
        _refuse(isinstance(result, dict), f"{label} result is invalid")
        _refuse(not result.get("error"), f"{label} has a runner/provider error")
        case_dir = (run_dir / "cases" / instance_id).resolve()
        _assert_within(case_dir, run_dir, f"{label} case")
        candidate_path = case_dir / "candidate_changes.diff"
        trace_path = case_dir / "trace.json"
        usage_report_path = case_dir / "usage_report.md"
        usage_path = case_dir / "usage.json"
        for path in (candidate_path, trace_path, usage_report_path, usage_path):
            _assert_within(path.resolve(), case_dir, f"{label} artifact")
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
            raise FormalArtifactRefused(
                f"cannot read {label} candidate patch: {exc}"
            ) from exc
        prediction_patch = prediction_by_id[instance_id].get("model_patch")
        _refuse(
            isinstance(prediction_patch, str), f"{label} prediction patch is invalid"
        )
        prediction_patch = cast(str, prediction_patch)
        _refuse(
            candidate_bytes == prediction_patch.encode("utf-8"),
            f"{label} candidate/prediction drift",
        )
        _refuse(
            result.get("patch_chars") == len(candidate_text),
            f"{label} patch size drift",
        )
        bucket = bucket_for_id[instance_id]
        is_empty = not candidate_bytes
        official_patch = (
            run_dir
            / "logs"
            / "run_evaluation"
            / run_id
            / prediction_name
            / instance_id
            / OFFICIAL_PATCH_FILENAME
        )
        _assert_within(official_patch.resolve(), run_dir, f"{label} official patch")
        if is_empty:
            _refuse(bucket == "empty_patch_ids", f"{label} empty-patch outcome drift")
            _refuse(
                not official_patch.exists(),
                f"{label} empty patch has unexpected evaluator patch bytes",
            )
        else:
            _refuse(
                bucket in {"resolved_ids", "unresolved_ids"},
                f"{label} non-empty patch lacks an official decision",
            )
            try:
                official_bytes = official_patch.read_bytes()
            except OSError as exc:
                raise FormalArtifactRefused(
                    f"cannot read {label} official patch: {exc}"
                ) from exc
            _refuse(
                candidate_bytes == official_bytes,
                f"{label} official patch-byte drift",
            )
            artifact_hashes[
                f"cases/{instance_id}/official-{OFFICIAL_PATCH_FILENAME}"
            ] = _sha256_file(official_patch, f"{label} official patch")
            patch_count += 1
        expected_official = OFFICIAL_STATUS_FOR_BUCKET[bucket]
        _refuse(
            result.get("official_evaluation_status") == expected_official,
            f"{label} result/official outcome drift",
        )
        case_score = score_by_id[instance_id]
        _refuse(isinstance(case_score, dict), f"{label} scorecard case is invalid")
        tokens, cost, case_failed_tools, _, case_transport_retries = _trace_metadata(
            trace_path, usage_path, expectation, command, label
        )
        expected_score = {
            "patch_chars": len(candidate_text),
            "patch_generated": not is_empty,
            "official_evaluation_status": expected_official,
            "official_evaluated": bucket in {"resolved_ids", "unresolved_ids"},
            "official_resolved": bucket == "resolved_ids",
            "total_tokens": tokens,
            "failed_tool_calls": case_failed_tools,
        }
        for key, expected in expected_score.items():
            _refuse(case_score.get(key) == expected, f"{label} scorecard {key} drift")
        _refuse(
            _as_decimal(case_score.get("estimated_cost_usd"), f"{label} score cost")
            == cost,
            f"{label} scorecard cost drift",
        )
        total_tokens += tokens
        total_cost += cost
        failed_tools += case_failed_tools
        transport_retries += case_transport_retries
        patch_binding_parts.extend(
            [instance_id.encode("utf-8"), b"\0", candidate_bytes, b"\0"]
        )
        for name, path in (
            ("candidate_changes.diff", candidate_path),
            ("trace.json", trace_path),
            ("usage.json", usage_path),
            ("usage_report.md", usage_report_path),
        ):
            artifact_hashes[f"cases/{instance_id}/{name}"] = _sha256_file(
                path, f"{label} {name}"
            )

    metrics = scorecard.get("metrics")
    _refuse(isinstance(metrics, dict), "scorecard metrics are missing")
    metrics = cast(dict[str, Any], metrics)
    metric_expectations = {
        "case_count": len(expectation.instance_ids),
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
    _refuse(
        _validate_expected_source_identity(expectation, root) == source_sha256,
        "expected source identity changed while validating formal run",
    )
    _refuse(
        _validate_frozen_inputs(expectation, root) == input_hashes,
        "frozen inputs changed while validating formal run",
    )
    return ValidatedFormalRun(
        run_id=run_id,
        planned=len(expectation.instance_ids),
        finalized=len(case_results),
        resolved=len(buckets["resolved_ids"]),
        unresolved=len(buckets["unresolved_ids"]),
        decided=len(buckets["resolved_ids"]) + len(buckets["unresolved_ids"]),
        empty=len(buckets["empty_patch_ids"]),
        infrastructure=len(buckets["error_ids"]) + len(buckets["incomplete_ids"]),
        failed_tools=failed_tools,
        transport_retries=transport_retries,
        tokens=total_tokens,
        cost=total_cost,
        patch_binding_parts=tuple(patch_binding_parts),
        artifact_sha256=dict(sorted(artifact_hashes.items())),
        command_argv_sha256=command_sha256,
        expected_source_identity_sha256=source_sha256,
        frozen_inputs_sha256=frozen_inputs_sha256,
        config_sha256=config_sha256,
    )


def _assert_result_config(
    results: Mapping[str, Any],
    scorecard: Mapping[str, Any],
    expectation: FormalRunExpectation,
    command: Mapping[str, Any],
    run_dir: Path,
    predictions_path: Path,
) -> None:
    dataset = _resolve_cli_path(
        expectation.project_root.resolve(), str(command["dataset"])
    )
    official_namespace = (
        "" if command["namespace_empty"] else str(command["official_namespace"])
    )
    expected_strings = {
        "provider": command["provider"],
        "model": command["model"],
        "split": command["split"],
        "thinking_mode": command["thinking_mode"],
        "reasoning_effort": command["reasoning_effort"],
        "agent_mode": "single",
        "profile": "",
        "tool_routing_mode": command["tool_routing"],
        "execution_mode": command["execution_mode"],
        "network_policy": command["network_policy"],
        "official_namespace": official_namespace,
        "container_runtime": command["container_runtime"],
        "container_image": command["container_image"],
        "container_memory": command["container_memory"],
    }
    for key, expected in expected_strings.items():
        _refuse(results.get(key) == expected, f"{expectation.label} {key} drift")
    expected_numbers = {
        "temperature": command["temperature"],
        "max_steps": command["max_steps"],
        "max_context_chars": command["max_context_chars"],
        "max_prompt_tokens": command["max_prompt_tokens"],
        "reserved_output_tokens": command["reserved_output_tokens"],
        "max_tool_calls_per_turn": command["max_tool_calls_per_turn"],
        "timeout_seconds": command["timeout_seconds"],
        "model_request_timeout_seconds": command["model_request_timeout_seconds"],
        "tool_execution_timeout_seconds": command["tool_execution_timeout_seconds"],
        "memory_recall_limit": command["memory_recall_limit"],
        "max_revision_rounds": 0,
        "container_cpus": command["container_cpus"],
        "container_pids_limit": command["container_pids_limit"],
    }
    if "--model-request-max-attempts" in expectation.command_argv:
        expected_numbers["model_request_max_attempts"] = command[
            "model_request_max_attempts"
        ]
    for key, expected in expected_numbers.items():
        _refuse(
            _as_decimal(results.get(key), f"results {key}")
            == _as_decimal(expected, f"command {key}"),
            f"{expectation.label} {key} drift",
        )
    for key in ("cost_budget_usd", "keep_worktree", "container_read_only"):
        _refuse(results.get(key) == command[key], f"{expectation.label} {key} drift")
    _refuse(results.get("skill_mode") == "auto", "formal skill mode drift")
    _refuse(
        results.get("skill_names") == [expectation.skill_name],
        "formal skill name drift",
    )
    _refuse(
        results.get("skill_manifest_sha256") == "builtins_only",
        "formal built-in skill source drift",
    )
    _refuse(
        results.get("memory_namespace") == "swebench:<instance_id>",
        "formal memory namespace drift",
    )
    _refuse(
        results.get("memory_snapshot_sha256") == "disabled",
        "formal memory snapshot drift",
    )
    _assert_path(results.get("dataset_name"), dataset, "results dataset")
    _assert_path(results.get("output_dir"), run_dir, "results output_dir")
    _assert_path(
        results.get("predictions_path"), predictions_path, "results predictions"
    )

    metadata = scorecard.get("metadata")
    _refuse(isinstance(metadata, dict), "scorecard metadata is missing")
    metadata = cast(dict[str, Any], metadata)
    metadata_expected = {
        **{key: value for key, value in expected_strings.items() if key != "model"},
        **expected_numbers,
        "requested_model": command["model"],
        "observed_models": [expectation.observed_model],
        "skill_mode": "auto",
        "skill_names": [expectation.skill_name],
        "skill_manifest_sha256": "builtins_only",
        "memory_namespace": "swebench:<instance_id>",
        "memory_snapshot_sha256": "disabled",
        "cost_budget_usd": command["cost_budget_usd"],
        "keep_worktree": command["keep_worktree"],
        "container_read_only": command["container_read_only"],
    }
    for key, expected in metadata_expected.items():
        _refuse(
            metadata.get(key) == expected, f"{expectation.label} scorecard {key} drift"
        )
    _assert_path(metadata.get("dataset_name"), dataset, "scorecard dataset")


def _trace_metadata(
    trace_path: Path,
    usage_path: Path,
    expectation: FormalRunExpectation,
    command: Mapping[str, Any],
    label: str,
) -> tuple[int, Decimal, int, int, int]:
    trace = _read_json(trace_path, f"{label} trace")
    events = trace.get("events")
    _refuse(isinstance(events, list), f"{label} trace events are missing")
    events = cast(list[Any], events)
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
                model_usage.get("provider") == command["provider"],
                f"{label} formal provider drift",
            )
            _refuse(
                model_usage.get("model") == command["model"],
                f"{label} requested model drift",
            )
            _refuse(
                model_usage.get("observed_models") == [expectation.observed_model],
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
            _refuse(
                1 <= attempts <= expectation.max_transport_attempts,
                f"{label} provider attempts drift",
            )
            error_codes_value = model_usage.get("error_codes")
            _refuse(
                isinstance(error_codes_value, list)
                and len(error_codes_value) <= attempts - 1
                and all(
                    str(item) in expectation.allowed_transport_error_codes
                    for item in error_codes_value
                ),
                f"{label} has non-retryable provider errors",
            )
            usage_source = str(model_usage.get("usage_source") or "")
            _refuse(
                usage_source in {"provider", "estimate"}, f"{label} usage source drift"
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
                    "provider": command["provider"],
                    "model": command["model"],
                    "provider_reported_models": [expectation.observed_model],
                    "fallback_used": False,
                    "fallback_provider": str(
                        model_usage.get("fallback_provider") or ""
                    ),
                    "fallback_model": str(model_usage.get("fallback_model") or ""),
                    "error_codes": [str(item) for item in error_codes_value],
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
    capability = cast(dict[str, Any], capability)
    _refuse(
        capability.get("native_tool_calling") is True, f"{label} native tools disabled"
    )
    capability_source = str(capability.get("source") or "")
    _refuse(
        str(command["provider"]) in capability_source
        and str(command["model"]) in capability_source,
        f"{label} capability source drift",
    )
    _refuse(len(skills) == 1, f"{label} skill selection metadata drift")
    skill_event = skills[0]
    _refuse(skill_event.get("skill_mode") == "auto", f"{label} skill mode drift")
    selected_skills = skill_event.get("skills")
    _refuse(isinstance(selected_skills, list), f"{label} selected skills are missing")
    selected_skills = cast(list[Any], selected_skills)
    _refuse(len(selected_skills) == 1, f"{label} selected skill count drift")
    selected_skill = selected_skills[0]
    _refuse(isinstance(selected_skill, dict), f"{label} selected skill is invalid")
    selected_skill = cast(dict[str, Any], selected_skill)
    _refuse(
        {
            "name": selected_skill.get("name"),
            "version": selected_skill.get("version"),
            "content_sha256": selected_skill.get("content_sha256"),
        }
        == {
            "name": expectation.skill_name,
            "version": expectation.skill_version,
            "content_sha256": expectation.skill_content_sha256,
        },
        f"{label} Skill identity drift",
    )
    _refuse(bool(trace_calls), f"{label} made no successful provider calls")

    usage = _read_json(usage_path, f"{label} usage")
    steps = usage.get("steps")
    _refuse(isinstance(steps, list), f"{label} usage steps are missing")
    steps = cast(list[Any], steps)
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
    summary = cast(dict[str, Any], summary)
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
        summary.get("active_skills") == [expectation.skill_name],
        f"{label} active Skill drift",
    )
    failed_tools = _as_int(summary.get("failed_tool_calls"), f"{label} failed tools")
    _refuse(failed_tools >= 0, f"{label} failed tool count is negative")
    transport_retries = sum(int(item["attempts"]) - 1 for item in trace_calls)
    return total_tokens, total_cost, failed_tools, len(trace_calls), transport_retries


def parse_formal_cli(argv: Sequence[str], label: str) -> dict[str, Any]:
    _refuse(
        list(argv[:3]) == [".venv/bin/forge", "bench", "swebench"],
        f"{label} entrypoint drift",
    )
    from agent_forge.cli.parser import build_parser

    parser = build_parser()
    destinations: dict[str, str] = {}
    stack = [parser]
    while stack:
        current = stack.pop()
        for action in current._actions:
            destinations.update(
                {option: str(action.dest) for option in action.option_strings}
            )
            if isinstance(action, _SubParsersAction):
                stack.extend(action.choices.values())
    seen: set[str] = set()
    repeatable = {"instance_id", "skill_manifest"}
    for token in argv[3:]:
        if not token.startswith("-"):
            continue
        _refuse("=" not in token, f"{label} forbids --flag=value")
        destination = destinations.get(token)
        _refuse(destination is not None, f"{label} has unsupported flag {token}")
        destination = cast(str, destination)
        _refuse(
            destination in repeatable or destination not in seen,
            f"{label} repeats {token}",
        )
        seen.add(destination)
    stderr = io.StringIO()
    try:
        with redirect_stderr(stderr):
            parsed = parser.parse_args(list(argv[1:]))
    except SystemExit as exc:
        detail = stderr.getvalue().strip().splitlines()
        raise FormalArtifactRefused(
            f"{label} public CLI parse failed: {detail[-1] if detail else 'invalid argv'}"
        ) from exc
    identity = vars(parsed)
    _refuse(identity.get("api_key") is None, f"{label} embeds an API key")
    return _json_compatible(identity)


def _validate_command_identity(
    command: Mapping[str, Any], expectation: FormalRunExpectation
) -> None:
    _refuse(
        command.get("command") == "bench" and command.get("bench_name") == "swebench",
        f"{expectation.label} command drift",
    )
    _refuse(
        command.get("instance_id") == list(expectation.instance_ids),
        f"{expectation.label} instance order drift",
    )
    _refuse(
        command.get("limit") == len(expectation.instance_ids),
        f"{expectation.label} limit drift",
    )
    _refuse(command.get("evaluate") is True, f"{expectation.label} evaluation disabled")
    _refuse(
        command.get("agent_mode") == "single", f"{expectation.label} agent mode drift"
    )
    _refuse(
        command.get("fallback_model", "") in {None, ""},
        f"{expectation.label} fallback drift",
    )
    _refuse(
        command.get("model_request_max_attempts") == expectation.max_transport_attempts,
        f"{expectation.label} model request attempts drift",
    )
    output = _resolve_cli_path(
        expectation.project_root.resolve(), str(command.get("output_root") or "")
    )
    _refuse(
        output == expectation.output_root.resolve(),
        f"{expectation.label} output root drift",
    )
    bound = {
        _resolve_cli_path(expectation.project_root.resolve(), path)
        for path, _ in expectation.frozen_inputs
    }
    for key in ("dataset", "cases_file"):
        path = _resolve_cli_path(
            expectation.project_root.resolve(), str(command.get(key) or "")
        )
        _refuse(path in bound, f"{expectation.label} {key} is not SHA-bound")
    for value in command.get("skill_manifest") or []:
        _refuse(
            _resolve_cli_path(expectation.project_root.resolve(), str(value)) in bound,
            f"{expectation.label} skill manifest is not SHA-bound",
        )


def _validate_expected_source_identity(
    expectation: FormalRunExpectation, project_root: Path
) -> str:
    source = expectation.expected_source_identity
    _refuse(
        source.get("binding") == "external_annotated_git_tag",
        "formal source binding drift",
    )
    tag = str(source.get("expected_tag") or "")
    _refuse(bool(tag), "formal source tag is missing")
    tag_ref = f"refs/tags/{tag}"
    _refuse(
        _git(project_root, "cat-file", "-t", tag_ref) == "tag",
        "formal source tag is not annotated",
    )
    revision = _git(project_root, "rev-parse", f"{tag_ref}^{{commit}}")
    manifest = expectation.expected_source_manifest_path.resolve()
    _assert_within(manifest, project_root, "formal source manifest")
    relative = manifest.relative_to(project_root).as_posix()
    process = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=project_root,
        check=False,
        capture_output=True,
    )
    _refuse(
        process.returncode == 0, "formal source manifest is not tracked by source tag"
    )
    try:
        current = manifest.read_bytes()
    except OSError as exc:
        raise FormalArtifactRefused(
            f"cannot read formal source manifest: {exc}"
        ) from exc
    _refuse(
        current == process.stdout, "formal source manifest differs from tagged blob"
    )
    return _json_sha256(
        {
            "binding": "external_annotated_git_tag",
            "tag": tag,
            "revision": revision,
            "manifest_blob_sha256": _sha256_bytes(process.stdout),
        }
    )


def _validate_frozen_inputs(
    expectation: FormalRunExpectation, project_root: Path
) -> dict[str, str]:
    actual: dict[str, str] = {}
    for raw_path, expected_sha256 in expectation.frozen_inputs:
        path = _resolve_cli_path(project_root, raw_path)
        _assert_within(path, project_root, f"frozen input {raw_path}")
        _refuse(raw_path not in actual, f"duplicate frozen input binding: {raw_path}")
        digest = _sha256_file(path, f"frozen input {raw_path}")
        _refuse(digest == expected_sha256, f"frozen input SHA-256 drift: {raw_path}")
        actual[raw_path] = digest
    _refuse(bool(actual), "formal run has no frozen input bindings")
    return dict(sorted(actual.items()))


def _read_predictions(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise FormalArtifactRefused(f"cannot read {label}: {exc}") from exc
    predictions: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        _refuse(bool(line.strip()), f"{label} contains an empty line")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FormalArtifactRefused(f"{label}[{index}] is invalid JSON") from exc
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


def _list_of_ids(aggregate: Mapping[str, Any], key: str, label: str) -> list[str]:
    value = aggregate.get(key)
    _refuse(isinstance(value, list), f"{label} {key} must be a list")
    value = cast(list[Any], value)
    result = [str(item) for item in value]
    _refuse(len(result) == len(set(result)), f"{label} {key} contains duplicates")
    return result


def _validate_official_aggregate(
    aggregate: Mapping[str, Any], instance_ids: tuple[str, ...], label: str
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
    counts = {
        "total_instances": len(instance_ids),
        "submitted_instances": len(instance_ids),
        "completed_instances": len(decided),
        "resolved_instances": len(buckets["resolved_ids"]),
        "unresolved_instances": len(buckets["unresolved_ids"]),
        "error_instances": 0,
        "empty_patch_instances": len(buckets["empty_patch_ids"]),
    }
    for key, expected_count in counts.items():
        _refuse(aggregate.get(key) == expected_count, f"{label} {key} drift")
    _refuse(aggregate.get("schema_version") == 2, f"{label} schema drift")
    return buckets


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FormalArtifactRefused(f"cannot read {label}: {exc}") from exc
    _refuse(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path, label: str) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise FormalArtifactRefused(f"cannot hash {label}: {exc}") from exc


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
        raise FormalArtifactRefused(f"{label} must be numeric") from exc
    _refuse(parsed.is_finite(), f"{label} must be finite")
    return parsed


def _assert_path(actual: Any, expected: Path, label: str) -> None:
    _refuse(isinstance(actual, str) and bool(actual), f"{label} path is missing")
    _refuse(Path(actual).resolve() == expected.resolve(), f"{label} path drift")


def _assert_within(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FormalArtifactRefused(f"{label} escapes its frozen root") from exc


def _resolve_cli_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else project_root / path).resolve()


def _git(project_root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=False,
        text=True,
        capture_output=True,
    )
    _refuse(
        process.returncode == 0,
        f"cannot validate formal source: {process.stderr.strip()}",
    )
    return process.stdout.strip()


def _json_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(raw)


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise FormalArtifactRefused(f"CLI identity contains unsupported type {type(value)}")


def _refuse(condition: bool, message: str) -> None:
    if not condition:
        raise FormalArtifactRefused(message)


__all__ = [
    "FormalArtifactRefused",
    "FormalRunExpectation",
    "ValidatedFormalRun",
    "parse_formal_cli",
    "validate_formal_run",
]
