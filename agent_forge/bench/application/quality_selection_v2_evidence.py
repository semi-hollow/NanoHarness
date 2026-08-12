"""把 v2 动态预检证据收敛为唯一校验层，并构造正式槽位计划。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from urllib.parse import urlsplit

from agent_forge.bench.application.campaign_lifecycle import (
    ExactImageIdentity,
    normalize_exact_image_identity,
)
from agent_forge.bench.application.formal_campaign import FormalCampaignSlot
from agent_forge.bench.application.formal_selection import ExpectedFormalSlot
from agent_forge.bench.application.quality_selection_v2 import (
    QualitySelectionV2Slot,
    slots_from_manifest,
)
from agent_forge.bench.formal_artifacts import FormalRunExpectation


class QualitySelectionV2EvidenceRefused(RuntimeError):
    """动态启动证据不完整或与预注册计划冲突。"""


@dataclass(frozen=True)
class QualitySelectionV2EvidencePlan:
    """首个正式 marker 前可持久化并在恢复时重建的纯证据计划。"""

    campaign_id: str
    identity_sha256: str
    expected_launch_source: Mapping[str, object]
    slots: tuple[FormalCampaignSlot, ...]
    expected_slots: tuple[ExpectedFormalSlot, ...]
    candidate_order: tuple[str, ...]
    campaign_inputs_path: Path
    campaign_inputs_sha256: str
    pacing_ledger_prefix_sha256: str


@dataclass(frozen=True)
class V2DynamicEvidence:
    """seal 与 FormalPlan 共用的动态证据校验结果。"""

    payload: Mapping[str, Any]
    campaign_inputs_path: Path | None
    campaign_inputs_sha256: str
    launch_source: Mapping[str, object]
    observed_models: Mapping[str, str]
    image_identities: tuple[ExactImageIdentity, ...]
    pacing_sha256: str
    pacing_bytes: int
    pacing_last_sequence: int
    formal_argv_sha256: tuple[tuple[str, str], ...]
    frozen_inputs: tuple[tuple[str, str], ...]
    readiness_sha256: str
    image_seal_sha256: str


_CAMPAIGN_ID = "showcase-quality-selection-v2"
_CANDIDATES = ("v4-pro", "glm")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_INPUT_KEYS = {
    "schema_version",
    "campaign_id",
    "status",
    "manifest",
    "source",
    "readiness",
    "pacing_ledger_prefix",
    "capability_artifacts",
    "qualification_artifacts",
    "candidate_observed_models",
    "image_seal",
    "transport_policy",
    "formal_command_argv_sha256",
}
_STATIC_INPUTS = {
    "protocol_sha256": "benchmarks/showcase/quality-selection-protocol-v2.json",
    "capability_probe_script_sha256": "scripts/probe_model_tool_contract.py",
    "capacity_probe_script_sha256": "scripts/probe_model_rate_limit_contract.py",
    "manifest_builder_script_sha256": "scripts/build_quality_selection_v2_manifest.py",
    "dataset_exporter_script_sha256": "scripts/export_showcase_datasets.py",
    "selection_summarizer_script_sha256": "scripts/summarize_quality_selection_v2.py",
    "campaign_runner_script_sha256": "scripts/run_quality_selection_v2.py",
    "development_set_provenance_verifier_sha256": "scripts/verify_golden_10_v2_provenance.py",
    "development_set_manifest_sha256": "benchmarks/regression/golden-10-v2.json",
    "image_manifest_sha256": "benchmarks/showcase/quality-selection-image-plan-v2.json",
    "skill_file_sha256": "agent_forge/skills/packages/swebench_repair/SKILL.md",
    "launcher_wrapper_sha256": ".venv/bin/forge",
}
_REQUIRED_SHARED = {
    "agent_forge/bench/formal_artifacts.py",
    "agent_forge/bench/application/campaign_lifecycle.py",
    "agent_forge/bench/application/formal_campaign.py",
    "agent_forge/bench/application/formal_selection.py",
    "agent_forge/bench/application/image_sealer.py",
    "agent_forge/bench/application/quality_selection_v2.py",
    "agent_forge/bench/application/quality_selection_v2_evidence.py",
    "agent_forge/bench/adapters/campaign_files.py",
    "agent_forge/bench/adapters/docker_images.py",
    "agent_forge/bench/ports/campaign.py",
}


def compose_v2_campaign_inputs(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    launch_source: Mapping[str, object],
    readiness_path: Path,
    image_seal_path: Path,
) -> V2DynamicEvidence:
    """验证实时 preflight 文件并形成尚未发布的规范 payload。"""

    return _dynamic_evidence(
        root.resolve(),
        manifest_path,
        manifest,
        None,
        launch_source=launch_source,
        readiness_path=readiness_path,
        image_seal_path=image_seal_path,
    )


def validate_v2_campaign_inputs(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    campaign_inputs_path: Path,
) -> V2DynamicEvidence:
    """只读重验已发布 payload；ledger 可在已封存 prefix 后继续增长。"""

    return _dynamic_evidence(
        root.resolve(),
        manifest_path,
        manifest,
        campaign_inputs_path,
        launch_source=None,
        readiness_path=None,
        image_seal_path=None,
    )


def build_v2_evidence_plan(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    campaign_inputs_path: Path,
) -> QualitySelectionV2EvidencePlan:
    """校验全部静态/动态绑定并机械构造二十个正式槽位。"""

    project_root = root.resolve()
    manifest_file, planned, commands, candidate_order = _schedule(
        project_root, manifest_path, manifest
    )
    frozen = _static_bindings(project_root, manifest_file, manifest)
    dynamic = validate_v2_campaign_inputs(
        project_root, manifest_file, manifest, campaign_inputs_path
    )
    assert dynamic.campaign_inputs_path is not None
    frozen.update(dynamic.frozen_inputs)
    frozen[_relative(project_root, dynamic.campaign_inputs_path)] = (
        dynamic.campaign_inputs_sha256
    )
    artifact_root = _under(project_root, str(manifest.get("artifact_root") or ""))
    source_binding = _object(manifest.get("source_identity"), "source binding")
    images = {identity["tag"]: identity for identity in dynamic.image_identities}
    formal_slots: list[FormalCampaignSlot] = []
    expected_slots: list[ExpectedFormalSlot] = []
    for slot, command in zip(planned, commands, strict=True):
        image = images.get(slot.image_tag)
        observed = dynamic.observed_models.get(slot.candidate_id)
        if image is None or observed is None:
            raise QualitySelectionV2EvidenceRefused(
                "formal dynamic identity is missing"
            )
        expectation = FormalRunExpectation(
            label=slot.slot_id,
            project_root=project_root,
            artifact_root=artifact_root,
            output_root=slot.output_root,
            instance_ids=(slot.case_id,),
            command_argv=slot.argv,
            expected_source_identity=dict(source_binding),
            expected_source_manifest_path=manifest_file,
            frozen_inputs=tuple(sorted(frozen.items())),
            observed_model=observed,
            skill_name="swebench_repair",
            skill_version="3.0.0",
            skill_content_sha256=str(manifest["skill_file_sha256"]),
            max_transport_attempts=1,
            allowed_transport_error_codes=frozenset(),
        )
        formal_slots.append(
            FormalCampaignSlot(
                slot_id=slot.slot_id,
                lease_group=str(command.get("shard") or ""),
                expectation=expectation,
                expected_image_identity=image,
            )
        )
        expected_slots.append(
            ExpectedFormalSlot(slot.slot_id, slot.candidate_id, slot.case_id)
        )
    manifest_sha = _sha256_file(manifest_file)
    argv_claims = dict(dynamic.formal_argv_sha256)
    identity = _json_sha256(
        {
            "schema_version": 1,
            "campaign_id": _CAMPAIGN_ID,
            "manifest_sha256": manifest_sha,
            "campaign_inputs_sha256": dynamic.campaign_inputs_sha256,
            "source": dynamic.payload["source"],
            "readiness_sha256": dynamic.readiness_sha256,
            "pacing_ledger_prefix_sha256": dynamic.pacing_sha256,
            "candidate_observed_models": dynamic.observed_models,
            "image_seal_sha256": dynamic.image_seal_sha256,
            "image_identities": list(dynamic.image_identities),
            "transport_policy": dynamic.payload["transport_policy"],
            "slots": [
                {
                    "slot_id": slot.slot_id,
                    "candidate_id": slot.candidate_id,
                    "case_id": slot.case_id,
                    "image_tag": slot.image_tag,
                    "output_root": _relative(project_root, slot.output_root),
                    "argv_sha256": argv_claims[slot.slot_id],
                }
                for slot in planned
            ],
            "frozen_inputs": dict(sorted(frozen.items())),
        }
    )
    return QualitySelectionV2EvidencePlan(
        campaign_id=_CAMPAIGN_ID,
        identity_sha256=identity,
        expected_launch_source=dynamic.launch_source,
        slots=tuple(formal_slots),
        expected_slots=tuple(expected_slots),
        candidate_order=candidate_order,
        campaign_inputs_path=dynamic.campaign_inputs_path,
        campaign_inputs_sha256=dynamic.campaign_inputs_sha256,
        pacing_ledger_prefix_sha256=dynamic.pacing_sha256,
    )


def _dynamic_evidence(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    inputs_path: Path | None,
    *,
    launch_source: Mapping[str, object] | None,
    readiness_path: Path | None,
    image_seal_path: Path | None,
) -> V2DynamicEvidence:
    manifest_file, slots, _, _ = _schedule(root, manifest_path, manifest)
    manifest_sha = _sha256_file(manifest_file)
    inputs_file: Path | None = None
    inputs_sha = ""
    if inputs_path is None:
        if launch_source is None or readiness_path is None or image_seal_path is None:
            raise QualitySelectionV2EvidenceRefused(
                "dynamic evidence inputs are missing"
            )
        source_claim = _source_claim(manifest, manifest_sha, launch_source)
        launch_identity = dict(launch_source)
        readiness_claim, readiness_sha = _readiness(root, readiness_path, None)
        pacing_claim = _pacing(root, manifest, None)
        image_claim, image_sha, images = _images(root, slots, image_seal_path, None)
        cap_claims, qual_claims, observed, provider_frozen = _providers(
            root, manifest, None, None
        )
        argv_claims = _argv_claims(slots)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "campaign_id": _CAMPAIGN_ID,
            "status": "sealed_before_first_formal_marker",
            "manifest": {
                "path": _relative(root, manifest_file),
                "sha256": manifest_sha,
            },
            "source": source_claim,
            "readiness": readiness_claim,
            "pacing_ledger_prefix": pacing_claim,
            "capability_artifacts": cap_claims,
            "qualification_artifacts": qual_claims,
            "candidate_observed_models": observed,
            "image_seal": image_claim,
            "transport_policy": {"max_attempts": 1, "allowed_error_codes": []},
            "formal_command_argv_sha256": argv_claims,
        }
    else:
        inputs_file = _under(root, inputs_path)
        payload = _read_json(inputs_file, "campaign inputs")
        inputs_sha = _sha256_file(inputs_file)
        _exact(payload, _INPUT_KEYS, "campaign inputs")
        if (
            payload.get("schema_version") != 1
            or payload.get("campaign_id") != _CAMPAIGN_ID
            or payload.get("status") != "sealed_before_first_formal_marker"
        ):
            raise QualitySelectionV2EvidenceRefused("campaign inputs seal drift")
        _file_claim(
            root,
            payload.get("manifest"),
            "command manifest claim",
            expected_path=manifest_file,
            expected_sha=manifest_sha,
        )
        launch_identity = _validate_source(
            payload.get("source"), manifest, manifest_sha
        )
        readiness_claim, readiness_sha = _readiness(
            root, None, payload.get("readiness")
        )
        pacing_claim = _pacing(root, manifest, payload.get("pacing_ledger_prefix"))
        image_claim, image_sha, images = _images(
            root, slots, None, payload.get("image_seal")
        )
        cap_claims, qual_claims, observed, provider_frozen = _providers(
            root,
            manifest,
            payload.get("capability_artifacts"),
            payload.get("qualification_artifacts"),
        )
        if payload.get("candidate_observed_models") != observed:
            raise QualitySelectionV2EvidenceRefused("candidate observed-model drift")
        if payload.get("transport_policy") != {
            "max_attempts": 1,
            "allowed_error_codes": [],
        }:
            raise QualitySelectionV2EvidenceRefused("formal transport policy drift")
        argv_claims = _argv_claims(slots)
        if payload.get("formal_command_argv_sha256") != argv_claims:
            raise QualitySelectionV2EvidenceRefused("formal argv claim drift")
    dynamic_frozen = dict(provider_frozen)
    dynamic_frozen[str(readiness_claim["path"])] = readiness_sha
    dynamic_frozen[str(image_claim["path"])] = image_sha
    prefix = cast(dict[str, Any], pacing_claim)
    return V2DynamicEvidence(
        payload=payload,
        campaign_inputs_path=inputs_file,
        campaign_inputs_sha256=inputs_sha,
        launch_source=launch_identity,
        observed_models=observed,
        image_identities=images,
        pacing_sha256=str(prefix["sha256"]),
        pacing_bytes=int(prefix["byte_length"]),
        pacing_last_sequence=int(prefix["last_sequence"]),
        formal_argv_sha256=tuple(
            (str(item["slot_id"]), str(item["sha256"])) for item in argv_claims
        ),
        frozen_inputs=tuple(sorted(dynamic_frozen.items())),
        readiness_sha256=readiness_sha,
        image_seal_sha256=image_sha,
    )


def _schedule(
    root: Path, manifest_path: Path, manifest: Mapping[str, Any]
) -> tuple[
    Path, tuple[QualitySelectionV2Slot, ...], list[dict[str, Any]], tuple[str, ...]
]:
    manifest_file = _under(root, manifest_path)
    if _read_json(manifest_file, "command manifest") != dict(manifest):
        raise QualitySelectionV2EvidenceRefused("command manifest object drift")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("manifest_id") != "showcase-quality-v2-selection-commands"
        or manifest.get("planned_starts") != 20
    ):
        raise QualitySelectionV2EvidenceRefused("invalid v2 command manifest")
    fixed = _argv(manifest.get("fixed_argv"), "formal fixed argv")
    if _flag(fixed, "--model-request-max-attempts") != "1":
        raise QualitySelectionV2EvidenceRefused("formal transport attempts are not one")
    provider_entries = [
        *_objects(manifest.get("capability_probes"), "capabilities"),
        *_objects(manifest.get("qualification_commands"), "qualifications"),
    ]
    if any(
        _flag(entry.get("argv"), "--max-attempts") != "1" for entry in provider_entries
    ):
        raise QualitySelectionV2EvidenceRefused(
            "preflight transport attempts are not one"
        )
    try:
        slots = tuple(slots_from_manifest(manifest, root))
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise QualitySelectionV2EvidenceRefused("invalid formal slot schedule") from exc
    commands = _objects(manifest.get("commands"), "formal commands")
    candidates = tuple(
        str(item.get("candidate_id") or "")
        for item in _objects(manifest.get("capability_probes"), "capabilities")
    )
    if len(slots) != 20 or len(commands) != 20 or candidates != _CANDIDATES:
        raise QualitySelectionV2EvidenceRefused("v2 schedule denominator drift")
    if manifest.get("composed_commands_sha256") != _json_sha256(
        [list(slot.argv) for slot in slots]
    ):
        raise QualitySelectionV2EvidenceRefused("composed formal command hash drift")
    return manifest_file, slots, commands, candidates


def _providers(
    root: Path,
    manifest: Mapping[str, Any],
    raw_capability_claims: object | None,
    raw_qualification_claims: object | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], dict[str, str]]:
    capabilities = _objects(manifest.get("capability_probes"), "capabilities")
    qualifications = _objects(manifest.get("qualification_commands"), "qualifications")
    cap_claims = (
        None
        if raw_capability_claims is None
        else _objects(raw_capability_claims, "capability claims")
    )
    qual_claims = (
        None
        if raw_qualification_claims is None
        else _objects(raw_qualification_claims, "qualification claims")
    )
    if len(capabilities) != 2 or len(qualifications) != 4:
        raise QualitySelectionV2EvidenceRefused("provider command denominator drift")
    if cap_claims is not None and len(cap_claims) != 2:
        raise QualitySelectionV2EvidenceRefused("capability claim count drift")
    if qual_claims is not None and len(qual_claims) != 4:
        raise QualitySelectionV2EvidenceRefused("qualification claim count drift")
    formal_models = _formal_models(manifest)
    observed: dict[str, str] = {}
    hashes: dict[str, str] = {}
    frozen: dict[str, str] = {}
    built_caps: list[dict[str, Any]] = []
    for index, entry in enumerate(capabilities):
        candidate = str(entry.get("candidate_id") or "")
        argv = _argv(entry.get("argv"), "capability argv")
        path = _under(root, _flag(argv, "--output"))
        digest = _sha256_file(path)
        claim = {
            "candidate_id": candidate,
            "path": _relative(root, path),
            "sha256": digest,
        }
        if cap_claims is not None and cap_claims[index] != claim:
            raise QualitySelectionV2EvidenceRefused("capability artifact path drift")
        if _flag(argv, "--provider") != "opencode-go" or _flag(
            argv, "--model"
        ) != formal_models.get(candidate):
            raise QualitySelectionV2EvidenceRefused("capability provider/model drift")
        observed[candidate] = _validate_capability(
            _read_json(path, "capability artifact"), argv
        )
        hashes[candidate] = digest
        frozen[claim["path"]] = digest
        built_caps.append(claim)
    if tuple(observed) != _CANDIDATES:
        raise QualitySelectionV2EvidenceRefused("capability candidate order drift")
    built_quals: list[dict[str, Any]] = []
    for index, entry in enumerate(qualifications):
        candidate = str(entry.get("candidate_id") or "")
        if candidate not in observed:
            raise QualitySelectionV2EvidenceRefused("unknown qualification candidate")
        argv = _argv(entry.get("argv"), "qualification argv")
        path = _under(root, _flag(argv, "--output"))
        digest = _sha256_file(path)
        rounds = _validate_qualification(
            root,
            _read_json(path, "qualification artifact"),
            argv,
            observed[candidate],
            hashes[candidate],
            manifest,
        )
        qualification_claim: dict[str, Any] = {
            "qualification_id": str(entry.get("qualification_id") or ""),
            "candidate_id": candidate,
            "path": _relative(root, path),
            "sha256": digest,
            "rounds": rounds,
        }
        if qual_claims is not None and qual_claims[index] != qualification_claim:
            raise QualitySelectionV2EvidenceRefused("qualification round claims drift")
        frozen[qualification_claim["path"]] = digest
        frozen.update((str(item["path"]), str(item["sha256"])) for item in rounds)
        built_quals.append(qualification_claim)
    return built_caps, built_quals, observed, frozen


def _validate_capability(evidence: Mapping[str, Any], argv: Sequence[str]) -> str:
    observed = str(evidence.get("observed_response_model") or "")
    expected = {
        "schema_version": 1,
        "status": "passed",
        "provider": _flag(argv, "--provider"),
        "requested_model": _flag(argv, "--model"),
        "credential_source": "OPENCODE_GO_API_KEY",
        "max_attempts": 1,
        "round_trip_observed_response_model": observed,
        "tool_call_source": "native",
        "tool_call_count": 1,
        "tool_arguments_match": True,
        "round_trip_completed": True,
        "fallback_used": False,
        "attempts_per_call": [1, 1],
        "error_codes": [],
        "error_code": "",
    }
    invalid = not _has_values(evidence, expected)
    invalid = invalid or not observed or len(observed) > 512
    invalid = invalid or any(ord(character) < 32 for character in observed)
    if "--base-url" in argv:
        endpoint, digest = _endpoint_identity(_flag(argv, "--base-url"))
        invalid = invalid or evidence.get("base_url_origin_path") != endpoint
        invalid = invalid or evidence.get("base_url_sha256") != digest
    if invalid:
        raise QualitySelectionV2EvidenceRefused("capability evidence drift")
    return observed


def _validate_qualification(
    root: Path,
    evidence: Mapping[str, Any],
    argv: Sequence[str],
    observed: str,
    capability_sha: str,
    manifest: Mapping[str, Any],
) -> list[dict[str, object]]:
    count = int(_flag(argv, "--round-trips"))
    records = _objects(evidence.get("rounds"), "qualification rounds")
    if "--capability-preflight" in argv:
        capability = _under(root, _flag(argv, "--capability-preflight"))
        if _sha256_file(capability) != capability_sha:
            raise QualitySelectionV2EvidenceRefused(
                "qualification capability binding drift"
            )
    expected = {
        "schema_version": 1,
        "status": "passed",
        "provider": _flag(argv, "--provider"),
        "requested_model": _flag(argv, "--model"),
        "credential_source": "OPENCODE_GO_API_KEY",
        "max_attempts": 1,
        "round_trips": count,
        "completed_round_trips": count,
        "requests_per_round_trip": 2,
        "capability_preflight_sha256": capability_sha,
        "capability_probe_script_sha256": manifest.get(
            "capability_probe_script_sha256"
        ),
        "preflight_observed_response_model": observed,
        "observed_response_model": observed,
        "transport_clean_first_attempt": True,
        "fallback_used": False,
        "failure": "",
    }
    if not _has_values(evidence, expected) or len(records) != count:
        raise QualitySelectionV2EvidenceRefused("qualification evidence drift")
    output = _under(root, _flag(argv, "--output"))
    claims: list[dict[str, object]] = []
    for ordinal, record in enumerate(records, start=1):
        path = _under(root, str(record.get("artifact") or ""))
        digest = _sha256_file(path)
        expected_record = {
            "ordinal": ordinal,
            "exit_code": 0,
            "artifact_sha256": digest,
            "attempts_per_call": [1, 1],
            "error_codes": [],
            "observed_response_model": observed,
            "passed": True,
        }
        if (
            path != output.with_suffix("") / f"round-{ordinal:02d}.json"
            or not _has_values(record, expected_record)
            or _validate_capability(_read_json(path, "qualification child round"), argv)
            != observed
        ):
            raise QualitySelectionV2EvidenceRefused("qualification child round drift")
        claims.append(
            {"ordinal": ordinal, "path": _relative(root, path), "sha256": digest}
        )
    return claims


def _pacing(
    root: Path, manifest: Mapping[str, Any], raw_claim: object | None
) -> dict[str, object]:
    ledger = _under(root, str(manifest.get("ledger_path") or ""))
    raw = _read_bytes(ledger, "pacing ledger")
    if raw_claim is None:
        prefix = raw
    else:
        claim = _object(raw_claim, "pacing prefix")
        _exact(
            claim, {"path", "byte_length", "sha256", "last_sequence"}, "pacing prefix"
        )
        length = claim.get("byte_length")
        if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
            raise QualitySelectionV2EvidenceRefused("invalid pacing prefix byte length")
        prefix = raw[:length]
        if (
            len(prefix) != length
            or claim.get("path") != _relative(root, ledger)
            or claim.get("sha256") != hashlib.sha256(prefix).hexdigest()
        ):
            raise QualitySelectionV2EvidenceRefused("pacing ledger prefix hash drift")
    events = _jsonl_events(prefix)
    _validate_preflight_events(events, manifest)
    if (
        raw_claim is not None
        and cast(Mapping[str, Any], raw_claim).get("last_sequence") != 20
    ):
        raise QualitySelectionV2EvidenceRefused("pacing prefix denominator drift")
    return {
        "path": _relative(root, ledger),
        "byte_length": len(prefix),
        "sha256": hashlib.sha256(prefix).hexdigest(),
        "last_sequence": 20,
    }


def _validate_preflight_events(
    events: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]
) -> None:
    entries = [
        *_objects(manifest.get("capability_probes"), "capabilities"),
        *_objects(manifest.get("qualification_commands"), "qualifications"),
    ]
    pacing = _object(manifest.get("pacing"), "pacing policy")
    expected: list[tuple[str, object]] = [
        (
            "wait",
            (
                "initial_quiet",
                _positive_int(pacing.get("initial_quiet_seconds"), "initial quiet"),
            ),
        )
    ]
    for index, entry in enumerate(entries, start=1):
        expected.extend(
            (
                ("start", (index, entry)),
                ("complete", (index, entry)),
                (
                    "wait",
                    (
                        "between_provider_commands",
                        _positive_int(
                            pacing.get("minimum_seconds_between_provider_commands"),
                            "provider pacing",
                        ),
                    ),
                ),
            )
        )
    expected.append(
        (
            "wait",
            (
                "qualification_to_formal_cooldown",
                _positive_int(
                    pacing.get("qualification_to_formal_cooldown_seconds"),
                    "formal cooldown",
                ),
            ),
        )
    )
    if len(events) != len(expected) or len(events) != 20:
        raise QualitySelectionV2EvidenceRefused("pacing prefix denominator drift")
    pending: Mapping[str, Any] | None = None
    previous_boundary: float | None = None
    for sequence, (event, spec) in enumerate(
        zip(events, expected, strict=True), start=1
    ):
        if event.get("schema_version") != 1 or event.get("sequence") != sequence:
            raise QualitySelectionV2EvidenceRefused("pacing event sequence drift")
        kind, detail = spec
        if kind == "wait":
            phase, required = cast(tuple[str, int], detail)
            _event(
                event,
                {
                    "schema_version": 1,
                    "sequence": sequence,
                    "event_type": "pacing_wait",
                    "phase": phase,
                    "required_seconds": required,
                    "result": "passed",
                },
                {"started_monotonic", "ended_monotonic", "elapsed_seconds"},
                "pacing wait",
            )
            if not _valid_elapsed(event, required):
                raise QualitySelectionV2EvidenceRefused("pacing wait evidence drift")
            wait_started = float(cast(int | float, event.get("started_monotonic")))
            wait_ended = float(cast(int | float, event.get("ended_monotonic")))
            if previous_boundary is not None and wait_started < previous_boundary:
                raise QualitySelectionV2EvidenceRefused("pacing event timeline overlap")
            previous_boundary = wait_ended
            continue
        provider_sequence, provider_entry = cast(tuple[int, Mapping[str, Any]], detail)
        candidate = str(provider_entry.get("candidate_id") or "")
        argv_sha = _json_sha256(
            list(_argv(provider_entry.get("argv"), "provider argv"))
        )
        if kind == "start":
            _event(
                event,
                {
                    "schema_version": 1,
                    "sequence": sequence,
                    "event_type": "provider_command_started",
                    "candidate_id": candidate,
                    "provider_sequence": provider_sequence,
                    "argv_sha256": argv_sha,
                },
                {"started_monotonic"},
                "provider start",
            )
            if not _finite(event.get("started_monotonic")):
                raise QualitySelectionV2EvidenceRefused("provider start evidence drift")
            started = float(cast(int | float, event.get("started_monotonic")))
            if previous_boundary is not None and started < previous_boundary:
                raise QualitySelectionV2EvidenceRefused("pacing event timeline overlap")
            previous_boundary = started
            pending = event
            continue
        _event(
            event,
            {
                "schema_version": 1,
                "sequence": sequence,
                "event_type": "provider_command_completed",
                "candidate_id": candidate,
                "provider_sequence": provider_sequence,
                "argv_sha256": argv_sha,
                "started_monotonic": None
                if pending is None
                else pending.get("started_monotonic"),
                "exit_code": 0,
                "result": "passed",
            },
            {"ended_monotonic", "elapsed_seconds"},
            "provider completion",
        )
        if pending is None or not _valid_elapsed(event, 0):
            raise QualitySelectionV2EvidenceRefused(
                "provider completion evidence drift"
            )
        completed_started = float(cast(int | float, event.get("started_monotonic")))
        completed_ended = float(cast(int | float, event.get("ended_monotonic")))
        if previous_boundary is not None and completed_started < previous_boundary:
            raise QualitySelectionV2EvidenceRefused("pacing event timeline overlap")
        previous_boundary = completed_ended
        pending = None


def _readiness(
    root: Path, path: Path | None, raw_claim: object | None
) -> tuple[dict[str, str], str]:
    if raw_claim is None:
        assert path is not None
        actual = _under(root, path)
        digest = _sha256_file(actual)
        claim = {"path": _relative(root, actual), "sha256": digest}
    else:
        actual, digest = _file_claim(root, raw_claim, "readiness")
        claim = {"path": _relative(root, actual), "sha256": digest}
    readiness = _read_json(actual, "readiness")
    gates = readiness.get("gates")
    if (
        readiness.get("status") != "go_complete_denominator_capacity"
        or not isinstance(gates, dict)
        or gates.get("weekly_covers_complete_campaign") is not True
        or gates.get("use_balance_off") is not True
    ):
        raise QualitySelectionV2EvidenceRefused("readiness is not sealed GO")
    return claim, digest


def _images(
    root: Path,
    slots: Sequence[QualitySelectionV2Slot],
    path: Path | None,
    raw_claim: object | None,
) -> tuple[dict[str, str], str, tuple[ExactImageIdentity, ...]]:
    if raw_claim is None:
        assert path is not None
        actual = _under(root, path)
        digest = _sha256_file(actual)
        claim = {"path": _relative(root, actual), "sha256": digest}
    else:
        actual, digest = _file_claim(root, raw_claim, "image seal")
        claim = {"path": _relative(root, actual), "sha256": digest}
    seal = _read_json(actual, "image seal")
    tags = list(dict.fromkeys(slot.image_tag for slot in slots))
    plan = [{"tag": tag, "platform": "linux/amd64"} for tag in tags]
    entries = _objects(seal.get("entries"), "image seal entries")
    if (
        len(tags) != 10
        or seal.get("schema_version") != 1
        or seal.get("status") != "complete"
        or seal.get("plan") != plan
        or len(entries) != 10
    ):
        raise QualitySelectionV2EvidenceRefused("complete image seal drift")
    identities: list[ExactImageIdentity] = []
    for index, (entry, expected) in enumerate(zip(entries, plan, strict=True)):
        if (
            entry.get("index") != index
            or entry.get("tag") != expected["tag"]
            or entry.get("platform") != "linux/amd64"
            or entry.get("phase") != "complete"
        ):
            raise QualitySelectionV2EvidenceRefused("image seal entry drift")
        try:
            identities.append(
                normalize_exact_image_identity(
                    expected["tag"],
                    "linux/amd64",
                    _object(entry.get("identity"), "image identity"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise QualitySelectionV2EvidenceRefused("invalid image identity") from exc
    return claim, digest, tuple(identities)


def _source_claim(
    manifest: Mapping[str, Any], manifest_sha: str, launch: Mapping[str, object]
) -> dict[str, object]:
    source = _object(manifest.get("source_identity"), "source binding")
    claim: dict[str, object] = {
        "binding": "external_annotated_git_tag",
        "expected_tag": str(source.get("expected_tag") or ""),
        "tag_object_type": "tag",
        "peeled_revision": str(launch.get("revision") or ""),
        "tagged_manifest_blob_sha256": manifest_sha,
        "launch_source": dict(launch),
    }
    _validate_source(claim, manifest, manifest_sha)
    return claim


def _validate_source(
    raw: object, manifest: Mapping[str, Any], manifest_sha: str
) -> dict[str, object]:
    source = _object(raw, "source seal")
    _exact(
        source,
        {
            "binding",
            "expected_tag",
            "tag_object_type",
            "peeled_revision",
            "tagged_manifest_blob_sha256",
            "launch_source",
        },
        "source seal",
    )
    launch = _object(source.get("launch_source"), "launch source")
    _exact(
        launch, {"revision", "branch", "dirty", "working_tree_sha256"}, "launch source"
    )
    policy = _object(manifest.get("source_identity"), "source binding")
    revision = str(source.get("peeled_revision") or "")
    if (
        source.get("binding") != "external_annotated_git_tag"
        or source.get("expected_tag") != policy.get("expected_tag")
        or source.get("tag_object_type") != "tag"
        or not _REVISION.fullmatch(revision)
        or source.get("tagged_manifest_blob_sha256") != manifest_sha
        or launch.get("revision") != revision
        or not isinstance(launch.get("branch"), str)
        or not launch.get("branch")
        or launch.get("dirty") is not False
        or launch.get("working_tree_sha256") != ""
        or policy.get("binding") != "external_annotated_git_tag"
        or policy.get("require_clean_worktree_including_untracked") is not True
    ):
        raise QualitySelectionV2EvidenceRefused("annotated clean source seal drift")
    return cast(dict[str, object], launch)


def _static_bindings(
    root: Path, manifest_path: Path, manifest: Mapping[str, Any]
) -> dict[str, str]:
    bindings = {_relative(root, manifest_path): _sha256_file(manifest_path)}
    for field, raw_path in _STATIC_INPUTS.items():
        _bind_file(root, bindings, raw_path, manifest.get(field), field)
    artifact = _under(root, str(manifest.get("artifact_root") or ""))
    for field, path in (
        ("dataset_binding_sha256", artifact / "dataset-binding.json"),
        ("agent_dataset_sha256", artifact / "dataset/agent-cases.json"),
        ("official_dataset_sha256", artifact / "dataset/official-cases.json"),
    ):
        _bind_file(root, bindings, path, manifest.get(field), field)
    shared = manifest.get("shared_implementation_sha256")
    if not isinstance(shared, dict) or not _REQUIRED_SHARED.issubset(shared):
        raise QualitySelectionV2EvidenceRefused("shared implementation binding drift")
    for raw_path, digest in shared.items():
        if not isinstance(raw_path, str):
            raise QualitySelectionV2EvidenceRefused("shared path is not a string")
        _bind_file(root, bindings, raw_path, digest, "shared implementation")
    skill = _under(root, _STATIC_INPUTS["skill_file_sha256"])
    prefix = skill.read_text(encoding="utf-8")[:512]
    if not re.search(r"(?m)^name: swebench_repair$", prefix) or not re.search(
        r"(?m)^version: 3\.0\.0$", prefix
    ):
        raise QualitySelectionV2EvidenceRefused("Skill identity drift")
    return bindings


def _formal_models(manifest: Mapping[str, Any]) -> dict[str, str]:
    fixed = _argv(manifest.get("fixed_argv"), "formal fixed argv")
    models: dict[str, str] = {}
    for command in _objects(manifest.get("commands"), "formal commands"):
        candidate = str(command.get("candidate_id") or "")
        model = _flag(
            (*fixed, *_argv(command.get("argv_suffix"), "formal suffix")), "--model"
        )
        if candidate in models and models[candidate] != model:
            raise QualitySelectionV2EvidenceRefused("formal candidate model drift")
        models[candidate] = model
    if tuple(models) != _CANDIDATES:
        raise QualitySelectionV2EvidenceRefused("formal candidate order drift")
    return models


def _argv_claims(slots: Sequence[QualitySelectionV2Slot]) -> list[dict[str, str]]:
    return [
        {"slot_id": slot.slot_id, "sha256": _json_sha256(list(slot.argv))}
        for slot in slots
    ]


def _file_claim(
    root: Path,
    raw: object,
    label: str,
    *,
    expected_path: Path | None = None,
    expected_sha: str | None = None,
) -> tuple[Path, str]:
    claim = _object(raw, f"{label} claim")
    _exact(claim, {"path", "sha256"}, f"{label} claim")
    path = _under(root, str(claim.get("path") or ""))
    digest = str(claim.get("sha256") or "")
    if (
        not _SHA256.fullmatch(digest)
        or _sha256_file(path) != digest
        or (expected_path is not None and path != expected_path)
        or (expected_sha is not None and digest != expected_sha)
    ):
        raise QualitySelectionV2EvidenceRefused(f"{label} artifact hash drift")
    return path, digest


def _bind_file(
    root: Path,
    bindings: dict[str, str],
    raw_path: str | Path,
    raw_digest: object,
    label: str,
) -> None:
    path = _under(root, raw_path)
    digest = str(raw_digest or "")
    if not _SHA256.fullmatch(digest) or _sha256_file(path) != digest:
        raise QualitySelectionV2EvidenceRefused(f"{label} SHA-256 drift")
    relative = _relative(root, path)
    if relative in bindings and bindings[relative] != digest:
        raise QualitySelectionV2EvidenceRefused(f"duplicate frozen input: {relative}")
    bindings[relative] = digest


def _jsonl_events(raw: bytes) -> list[dict[str, Any]]:
    if not raw or not raw.endswith(b"\n"):
        raise QualitySelectionV2EvidenceRefused("pacing ledger prefix is incomplete")
    try:
        values = [
            json.loads(line, parse_constant=_reject_constant)
            for line in raw.decode("utf-8").splitlines()
        ]
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise QualitySelectionV2EvidenceRefused("invalid pacing ledger prefix") from exc
    if any(not isinstance(value, dict) for value in values):
        raise QualitySelectionV2EvidenceRefused("pacing ledger event is not an object")
    return cast(list[dict[str, Any]], values)


def _valid_elapsed(event: Mapping[str, Any], required: int) -> bool:
    values = [
        event.get(key)
        for key in ("started_monotonic", "ended_monotonic", "elapsed_seconds")
    ]
    if any(not _finite(value) for value in values):
        return False
    started, ended, elapsed = (float(cast(int | float, value)) for value in values)
    return (
        ended >= started
        and elapsed >= required
        and math.isclose(ended - started, elapsed, rel_tol=1e-9, abs_tol=1e-6)
    )


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _endpoint_identity(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    endpoint = f"{parsed.scheme}://{parsed.hostname or ''}"
    if parsed.port:
        endpoint += f":{parsed.port}"
    endpoint += parsed.path.rstrip("/")
    return endpoint, hashlib.sha256(base_url.encode()).hexdigest()


def _under(root: Path, raw: str | Path) -> Path:
    path = Path(raw)
    candidate = path if path.is_absolute() else root / path
    if candidate.is_symlink():
        raise QualitySelectionV2EvidenceRefused(f"path cannot be a symlink: {raw}")
    resolved = candidate.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise QualitySelectionV2EvidenceRefused(f"path escapes project root: {raw}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise QualitySelectionV2EvidenceRefused("path escapes project root") from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise QualitySelectionV2EvidenceRefused(f"{label} is not a regular file")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise QualitySelectionV2EvidenceRefused(f"cannot read {label}") from exc
    return _object(value, label)


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise QualitySelectionV2EvidenceRefused(f"{label} is not a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise QualitySelectionV2EvidenceRefused(f"cannot read {label}") from exc


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(_read_bytes(path, "frozen file")).hexdigest()


def _json_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise QualitySelectionV2EvidenceRefused("value is not strict JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _argv(raw: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(item, str) or not item for item in raw
    ):
        raise QualitySelectionV2EvidenceRefused(f"{label} is invalid")
    return tuple(cast(Sequence[str], raw))


def _flag(raw_argv: object, flag: str) -> str:
    argv = _argv(raw_argv, "command argv")
    positions = [index for index, item in enumerate(argv) if item == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise QualitySelectionV2EvidenceRefused(f"command flag drift: {flag}")
    return argv[positions[0] + 1]


def _object(raw: object, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise QualitySelectionV2EvidenceRefused(f"{label} must be an object")
    return cast(dict[str, Any], raw)


def _objects(raw: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise QualitySelectionV2EvidenceRefused(f"{label} must be an object list")
    return cast(list[dict[str, Any]], raw)


def _positive_int(raw: object, label: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise QualitySelectionV2EvidenceRefused(f"{label} must be positive")
    return raw


def _exact(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise QualitySelectionV2EvidenceRefused(f"{label} keys drift")


def _has_values(value: Mapping[str, Any], expected: Mapping[str, object]) -> bool:
    return all(
        value.get(key) is wanted
        if isinstance(wanted, bool)
        else value.get(key) == wanted
        for key, wanted in expected.items()
    )


def _event(
    value: Mapping[str, Any],
    expected: Mapping[str, object],
    variable_keys: set[str],
    label: str,
) -> None:
    if set(value) != set(expected) | variable_keys or not _has_values(value, expected):
        raise QualitySelectionV2EvidenceRefused(f"{label} evidence drift")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


__all__ = [
    "QualitySelectionV2EvidencePlan",
    "QualitySelectionV2EvidenceRefused",
    "V2DynamicEvidence",
    "build_v2_evidence_plan",
    "compose_v2_campaign_inputs",
    "validate_v2_campaign_inputs",
]
