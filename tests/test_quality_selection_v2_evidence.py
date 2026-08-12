from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_forge.bench.application.quality_selection_v2_evidence import (
    QualitySelectionV2EvidenceRefused,
    build_v2_evidence_plan,
)


STATIC_PATHS = {
    "protocol_sha256": "benchmarks/showcase/quality-selection-protocol-v2.json",
    "capability_probe_script_sha256": "scripts/probe_model_tool_contract.py",
    "capacity_probe_script_sha256": "scripts/probe_model_rate_limit_contract.py",
    "manifest_builder_script_sha256": "scripts/build_quality_selection_v2_manifest.py",
    "dataset_exporter_script_sha256": "scripts/export_showcase_datasets.py",
    "selection_summarizer_script_sha256": "scripts/summarize_quality_selection_v2.py",
    "campaign_runner_script_sha256": "scripts/run_quality_selection_v2.py",
    "development_set_provenance_verifier_sha256": (
        "scripts/verify_golden_10_v2_provenance.py"
    ),
    "development_set_manifest_sha256": "benchmarks/regression/golden-10-v2.json",
    "image_manifest_sha256": (
        "benchmarks/showcase/quality-selection-image-plan-v2.json"
    ),
    "skill_file_sha256": "agent_forge/skills/packages/swebench_repair/SKILL.md",
    "launcher_wrapper_sha256": ".venv/bin/forge",
}
SHARED_PATHS = (
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
)


def _json_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def _capability(provider: str, model: str, observed: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "passed",
        "provider": provider,
        "requested_model": model,
        "credential_source": "OPENCODE_GO_API_KEY",
        "max_attempts": 1,
        "observed_response_model": observed,
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


@dataclass
class EvidenceFixture:
    root: Path
    manifest_path: Path
    inputs_path: Path
    manifest: dict[str, Any]
    inputs: dict[str, Any]

    def write_inputs(self) -> None:
        _write_json(self.root / self.inputs_path, self.inputs)

    def plan(self):  # type: ignore[no-untyped-def]
        return build_v2_evidence_plan(
            self.root, self.manifest_path, self.manifest, self.inputs_path
        )


def _rewrite_ledger(fixture: EvidenceFixture, mutation: Any) -> list[dict[str, Any]]:
    ledger = fixture.root / fixture.manifest["ledger_path"]
    events = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    mutation(events)
    payload = b"".join(
        json.dumps(event, sort_keys=True).encode() + b"\n" for event in events
    )
    ledger.write_bytes(payload)
    fixture.inputs["pacing_ledger_prefix"].update(
        byte_length=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        last_sequence=len(events),
    )
    fixture.write_inputs()
    return events


def _rewrite_claimed_json(
    fixture: EvidenceFixture,
    claim: dict[str, Any],
    mutation: Any,
) -> dict[str, Any]:
    path = fixture.root / claim["path"]
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    _write_json(path, value)
    claim["sha256"] = _sha(path)
    fixture.write_inputs()
    return value


def _fixture(tmp_path: Path) -> EvidenceFixture:
    artifact = Path(".agent_forge/v2")
    for field, raw_path in STATIC_PATHS.items():
        path = tmp_path / raw_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "---\nname: swebench_repair\nversion: 3.0.0\n---\n"
            if field == "skill_file_sha256"
            else f"bound:{field}\n"
        )
        path.write_text(content, encoding="utf-8")
    shared: dict[str, str] = {}
    for raw_path in SHARED_PATHS:
        path = tmp_path / raw_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"bound:{raw_path}\n", encoding="utf-8")
        shared[raw_path] = _sha(path)
    for artifact_path in (
        artifact / "dataset-binding.json",
        artifact / "dataset/agent-cases.json",
        artifact / "dataset/official-cases.json",
    ):
        _write_json(tmp_path / artifact_path, {"sealed": str(artifact_path)})

    candidates = {"v4-pro": "deepseek-v4-pro", "glm": "glm-5.2"}
    capabilities: list[dict[str, Any]] = []
    capability_claims: list[dict[str, Any]] = []
    observed = {
        candidate: f"observed/{model}" for candidate, model in candidates.items()
    }
    for candidate, model in candidates.items():
        output = artifact / "preflight" / f"{candidate}.json"
        argv = [
            "python",
            "probe.py",
            "--provider",
            "opencode-go",
            "--model",
            model,
            "--max-attempts",
            "1",
            "--output",
            str(output),
        ]
        capabilities.append({"candidate_id": candidate, "argv": argv})
        _write_json(
            tmp_path / output,
            _capability("opencode-go", model, observed[candidate]),
        )
        capability_claims.append(
            {
                "candidate_id": candidate,
                "path": str(output),
                "sha256": _sha(tmp_path / output),
            }
        )

    schedule = [
        ("v4-pro", "burst-01"),
        ("glm", "burst-01"),
        ("glm", "burst-02"),
        ("v4-pro", "burst-02"),
    ]
    qualifications: list[dict[str, Any]] = []
    qualification_claims: list[dict[str, Any]] = []
    cap_sha = {item["candidate_id"]: item["sha256"] for item in capability_claims}
    capability_script_sha = _sha(
        tmp_path / STATIC_PATHS["capability_probe_script_sha256"]
    )
    for candidate, burst in schedule:
        model = candidates[candidate]
        output = artifact / "qualification" / burst / f"{candidate}.json"
        qualification_id = f"{candidate}/{burst}"
        argv = [
            "python",
            "capacity.py",
            "--provider",
            "opencode-go",
            "--model",
            model,
            "--round-trips",
            "4",
            "--max-attempts",
            "1",
            "--output",
            str(output),
        ]
        qualifications.append(
            {
                "qualification_id": qualification_id,
                "candidate_id": candidate,
                "argv": argv,
            }
        )
        child_claims: list[dict[str, object]] = []
        records: list[dict[str, object]] = []
        for ordinal in range(1, 5):
            child = output.with_suffix("") / f"round-{ordinal:02d}.json"
            _write_json(
                tmp_path / child,
                _capability("opencode-go", model, observed[candidate]),
            )
            digest = _sha(tmp_path / child)
            child_claims.append(
                {"ordinal": ordinal, "path": str(child), "sha256": digest}
            )
            records.append(
                {
                    "ordinal": ordinal,
                    "exit_code": 0,
                    "artifact": str(child),
                    "artifact_sha256": digest,
                    "attempts_per_call": [1, 1],
                    "error_codes": [],
                    "observed_response_model": observed[candidate],
                    "passed": True,
                }
            )
        _write_json(
            tmp_path / output,
            {
                "schema_version": 1,
                "status": "passed",
                "provider": "opencode-go",
                "requested_model": model,
                "credential_source": "OPENCODE_GO_API_KEY",
                "max_attempts": 1,
                "round_trips": 4,
                "completed_round_trips": 4,
                "requests_per_round_trip": 2,
                "capability_preflight_sha256": cap_sha[candidate],
                "capability_probe_script_sha256": capability_script_sha,
                "preflight_observed_response_model": observed[candidate],
                "observed_response_model": observed[candidate],
                "transport_clean_first_attempt": True,
                "fallback_used": False,
                "failure": "",
                "rounds": records,
            },
        )
        qualification_claims.append(
            {
                "qualification_id": qualification_id,
                "candidate_id": candidate,
                "path": str(output),
                "sha256": _sha(tmp_path / output),
                "rounds": child_claims,
            }
        )

    fixed = [
        "forge",
        "bench",
        "swebench",
        "--model-request-max-attempts",
        "1",
    ]
    commands: list[dict[str, Any]] = []
    composed: list[list[str]] = []
    tags: list[str] = []
    ordinal = 0
    case_order = [
        ("v4-pro", "glm") if index % 4 in {0, 3} else ("glm", "v4-pro")
        for index in range(10)
    ]
    for case_index, pair in enumerate(case_order, start=1):
        case_id = f"org__repo-{case_index}"
        tag = f"swebench/sweb.eval.x86_64.org_1776_repo-{case_index}:latest"
        tags.append(tag)
        for pair_position, candidate in enumerate(pair, start=1):
            ordinal += 1
            output = artifact / "formal" / candidate / f"case-{case_index:02d}"
            suffix = [
                "--model",
                candidates[candidate],
                "--limit",
                "1",
                "--instance-id",
                case_id,
                "--output-root",
                str(output),
            ]
            composed.append([*fixed, *suffix])
            commands.append(
                {
                    "ordinal": ordinal,
                    "candidate_id": candidate,
                    "shard": f"case-{case_index:02d}",
                    "instance_ids": [case_id],
                    "image": {"tag": tag},
                    "output_root": str(output),
                    "argv_suffix": suffix,
                    "pair_position": pair_position,
                }
            )

    image_seal = artifact / "image-seal.json"
    image_entries: list[dict[str, Any]] = []
    for index, tag in enumerate(tags):
        digest = hashlib.sha256(tag.encode()).hexdigest()
        image_entries.append(
            {
                "index": index,
                "tag": tag,
                "platform": "linux/amd64",
                "phase": "complete",
                "identity": {
                    "tag": tag,
                    "repo_digest": f"{tag.rsplit(':', 1)[0]}@sha256:{digest}",
                    "image_id": f"sha256:{digest}",
                    "platform": "linux/amd64",
                },
            }
        )
    _write_json(
        tmp_path / image_seal,
        {
            "schema_version": 1,
            "status": "complete",
            "plan": [{"tag": tag, "platform": "linux/amd64"} for tag in tags],
            "entries": image_entries,
        },
    )
    readiness = artifact / "readiness.json"
    _write_json(
        tmp_path / readiness,
        {
            "status": "go_complete_denominator_capacity",
            "gates": {
                "weekly_covers_complete_campaign": True,
                "use_balance_off": True,
            },
        },
    )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": "showcase-quality-v2-selection-commands",
        "planned_starts": 20,
        "source_identity": {
            "binding": "external_annotated_git_tag",
            "expected_tag": "quality-v2-tag",
            "require_clean_worktree_including_untracked": True,
        },
        "artifact_root": str(artifact),
        "fixed_argv": fixed,
        "commands": commands,
        "composed_commands_sha256": _json_sha(composed),
        "capability_probes": capabilities,
        "qualification_commands": qualifications,
        "pacing": {
            "initial_quiet_seconds": 1800,
            "minimum_seconds_between_provider_commands": 300,
            "qualification_to_formal_cooldown_seconds": 900,
        },
        "ledger_path": str(artifact / "pacing-ledger.jsonl"),
        "shared_implementation_sha256": shared,
    }
    for field, raw_path in STATIC_PATHS.items():
        manifest[field] = _sha(tmp_path / raw_path)
    manifest.update(
        dataset_binding_sha256=_sha(tmp_path / artifact / "dataset-binding.json"),
        agent_dataset_sha256=_sha(tmp_path / artifact / "dataset/agent-cases.json"),
        official_dataset_sha256=_sha(
            tmp_path / artifact / "dataset/official-cases.json"
        ),
    )

    events: list[dict[str, Any]] = []
    clock = 0.0

    def wait_event(phase: str, seconds: int) -> None:
        nonlocal clock
        started = clock
        clock += seconds
        events.append(
            {
                "schema_version": 1,
                "sequence": len(events) + 1,
                "event_type": "pacing_wait",
                "phase": phase,
                "required_seconds": seconds,
                "started_monotonic": started,
                "ended_monotonic": clock,
                "elapsed_seconds": seconds,
                "result": "passed",
            }
        )

    wait_event("initial_quiet", 1800)
    for provider_sequence, entry in enumerate(
        [*capabilities, *qualifications], start=1
    ):
        started = clock
        argv_sha256 = _json_sha(entry["argv"])
        events.append(
            {
                "schema_version": 1,
                "sequence": len(events) + 1,
                "event_type": "provider_command_started",
                "candidate_id": entry["candidate_id"],
                "provider_sequence": provider_sequence,
                "argv_sha256": argv_sha256,
                "started_monotonic": started,
            }
        )
        clock += 2
        events.append(
            {
                "schema_version": 1,
                "sequence": len(events) + 1,
                "event_type": "provider_command_completed",
                "candidate_id": entry["candidate_id"],
                "provider_sequence": provider_sequence,
                "argv_sha256": argv_sha256,
                "started_monotonic": started,
                "ended_monotonic": clock,
                "elapsed_seconds": 2,
                "exit_code": 0,
                "result": "passed",
            }
        )
        wait_event("between_provider_commands", 300)
    wait_event("qualification_to_formal_cooldown", 900)
    ledger = tmp_path / manifest["ledger_path"]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger_bytes = b"".join(
        json.dumps(event, sort_keys=True).encode() + b"\n" for event in events
    )
    ledger.write_bytes(ledger_bytes)

    manifest_path = Path("benchmarks/showcase/manifest.json")
    _write_json(tmp_path / manifest_path, manifest)
    manifest_sha = _sha(tmp_path / manifest_path)
    inputs: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": "showcase-quality-selection-v2",
        "status": "sealed_before_first_formal_marker",
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha},
        "source": {
            "binding": "external_annotated_git_tag",
            "expected_tag": "quality-v2-tag",
            "tag_object_type": "tag",
            "peeled_revision": "a" * 40,
            "tagged_manifest_blob_sha256": manifest_sha,
            "launch_source": {
                "revision": "a" * 40,
                "branch": "agent/canonical-showcase",
                "dirty": False,
                "working_tree_sha256": "",
            },
        },
        "readiness": {"path": str(readiness), "sha256": _sha(tmp_path / readiness)},
        "pacing_ledger_prefix": {
            "path": manifest["ledger_path"],
            "byte_length": len(ledger_bytes),
            "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
            "last_sequence": len(events),
        },
        "capability_artifacts": capability_claims,
        "qualification_artifacts": qualification_claims,
        "candidate_observed_models": observed,
        "image_seal": {
            "path": str(image_seal),
            "sha256": _sha(tmp_path / image_seal),
        },
        "transport_policy": {"max_attempts": 1, "allowed_error_codes": []},
        "formal_command_argv_sha256": [
            {"slot_id": f"slot-{index:03d}", "sha256": _json_sha(argv)}
            for index, argv in enumerate(composed, start=1)
        ],
    }
    inputs_path = artifact / "campaign-inputs.json"
    _write_json(tmp_path / inputs_path, inputs)
    return EvidenceFixture(tmp_path, manifest_path, inputs_path, manifest, inputs)


def test_builds_twenty_hash_bound_formal_slots(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    plan = fixture.plan()

    assert plan.campaign_id == "showcase-quality-selection-v2"
    assert len(plan.slots) == len(plan.expected_slots) == 20
    assert plan.candidate_order == ("v4-pro", "glm")
    assert fixture.inputs["pacing_ledger_prefix"]["last_sequence"] == 20
    assert len(plan.identity_sha256) == 64
    assert plan.expected_launch_source == {
        "revision": "a" * 40,
        "branch": "agent/canonical-showcase",
        "dirty": False,
        "working_tree_sha256": "",
    }
    assert [slot.slot_id for slot in plan.slots] == [
        f"slot-{index:03d}" for index in range(1, 21)
    ]
    for first, second in zip(plan.slots[::2], plan.slots[1::2], strict=True):
        assert first.lease_group == second.lease_group
        assert first.expected_image_identity == second.expected_image_identity
    for slot in plan.slots:
        expectation = slot.expectation
        assert expectation.max_transport_attempts == 1
        assert expectation.allowed_transport_error_codes == frozenset()
        assert expectation.observed_model.startswith("observed/")
        frozen = dict(expectation.frozen_inputs)
        assert str(fixture.inputs_path) in frozen
        assert fixture.manifest["ledger_path"] not in frozen
        assert len(frozen) >= 40


def test_identity_uses_only_sealed_ledger_prefix_when_ledger_grows(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first = fixture.plan()
    ledger = tmp_path / fixture.manifest["ledger_path"]
    with ledger.open("ab") as stream:
        stream.write(b'{"sequence":15,"event_type":"future_formal_event"}\n')

    second = fixture.plan()

    assert second.identity_sha256 == first.identity_sha256
    assert second.pacing_ledger_prefix_sha256 == first.pacing_ledger_prefix_sha256


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda fixture: fixture.inputs["transport_policy"].update(max_attempts=2),
            "transport policy",
        ),
        (
            lambda fixture: fixture.inputs["source"]["launch_source"].update(
                dirty=True
            ),
            "source seal",
        ),
        (
            lambda fixture: fixture.inputs["candidate_observed_models"].update(
                glm="different"
            ),
            "observed-model",
        ),
        (
            lambda fixture: fixture.inputs["formal_command_argv_sha256"][0].update(
                sha256="0" * 64
            ),
            "argv claim",
        ),
    ],
)
def test_dynamic_seal_drift_is_fail_closed(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    fixture = _fixture(tmp_path)
    mutation(fixture)
    fixture.write_inputs()

    with pytest.raises(QualitySelectionV2EvidenceRefused, match=message):
        fixture.plan()


def test_child_round_byte_drift_is_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    child = tmp_path / fixture.inputs["qualification_artifacts"][0]["rounds"][0]["path"]
    child.write_text("{}\n", encoding="utf-8")

    with pytest.raises(QualitySelectionV2EvidenceRefused, match="child round drift"):
        fixture.plan()


def test_pacing_prefix_drift_is_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.inputs["pacing_ledger_prefix"]["sha256"] = "0" * 64
    fixture.write_inputs()

    with pytest.raises(QualitySelectionV2EvidenceRefused, match="prefix hash drift"):
        fixture.plan()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda events: events[1].update(candidate_id="glm"), "start evidence"),
        (
            lambda events: events[2].update(provider_sequence=2),
            "completion evidence",
        ),
        (
            lambda events: events[2].update(started_monotonic=1.0),
            "completion evidence",
        ),
        (lambda events: events[2].update(exit_code=1), "completion evidence"),
        (lambda events: events[2].update(sequence=99), "event sequence"),
    ],
)
def test_provider_start_completion_pair_is_fail_closed(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_ledger(fixture, mutation)

    with pytest.raises(QualitySelectionV2EvidenceRefused, match=message):
        fixture.plan()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda events: events[1].update(started_monotonic=1799.0),
        lambda events: events[3].update(
            started_monotonic=1801.0,
            ended_monotonic=2101.0,
            elapsed_seconds=300.0,
        ),
    ],
)
def test_preflight_pacing_prefix_refuses_timeline_overlap(
    tmp_path: Path, mutation: Any
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_ledger(fixture, mutation)

    with pytest.raises(QualitySelectionV2EvidenceRefused, match="timeline overlap"):
        fixture.plan()


def test_capability_max_attempts_and_attempt_records_are_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    claim = fixture.inputs["capability_artifacts"][0]
    _rewrite_claimed_json(
        fixture,
        claim,
        lambda value: value.update(max_attempts=2, attempts_per_call=[1, 2]),
    )

    with pytest.raises(QualitySelectionV2EvidenceRefused, match="capability evidence"):
        fixture.plan()


def test_qualification_max_attempts_is_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    claim = fixture.inputs["qualification_artifacts"][0]
    _rewrite_claimed_json(fixture, claim, lambda value: value.update(max_attempts=2))

    with pytest.raises(
        QualitySelectionV2EvidenceRefused, match="qualification evidence"
    ):
        fixture.plan()


def test_child_round_max_attempts_and_errors_are_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    qualification_claim = fixture.inputs["qualification_artifacts"][0]
    child_claim = qualification_claim["rounds"][0]
    child_path = fixture.root / child_claim["path"]
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child.update(max_attempts=2, error_codes=["server_error"])
    _write_json(child_path, child)
    child_claim["sha256"] = _sha(child_path)
    qualification_path = fixture.root / qualification_claim["path"]
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    qualification["rounds"][0]["artifact_sha256"] = child_claim["sha256"]
    _write_json(qualification_path, qualification)
    qualification_claim["sha256"] = _sha(qualification_path)
    fixture.write_inputs()

    with pytest.raises(QualitySelectionV2EvidenceRefused, match="capability evidence"):
        fixture.plan()


def test_invalid_image_identity_is_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    image_path = tmp_path / fixture.inputs["image_seal"]["path"]
    image = json.loads(image_path.read_text(encoding="utf-8"))
    image["entries"][0]["identity"]["image_id"] = "sha256:short"
    _write_json(image_path, image)
    fixture.inputs["image_seal"]["sha256"] = _sha(image_path)
    fixture.write_inputs()

    with pytest.raises(QualitySelectionV2EvidenceRefused, match="image identity"):
        fixture.plan()


def test_missing_shared_evidence_module_binding_is_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    del fixture.manifest["shared_implementation_sha256"][
        "agent_forge/bench/application/quality_selection_v2_evidence.py"
    ]
    _write_json(tmp_path / fixture.manifest_path, fixture.manifest)

    with pytest.raises(
        QualitySelectionV2EvidenceRefused, match="shared implementation"
    ):
        fixture.plan()
