from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from agent_forge.bench.application.quality_selection_v2 import (
    QualitySelectionV2FormalLauncher,
    QualitySelectionV2Preflight,
    QualitySelectionV2Refused,
    QualitySelectionV2Slot,
    audit_quality_selection_v2_completed_pacing,
    slots_from_manifest,
)


ROOT = Path(__file__).parents[1]
MANIFEST = json.loads(
    (ROOT / "benchmarks/showcase/quality-selection-command-manifest-v2.json").read_text(
        encoding="utf-8"
    )
)


class FakeImageSealer:
    def __init__(self, callback):  # type: ignore[no-untyped-def]
        self._callback = callback

    def seal(self, requests):  # type: ignore[no-untyped-def]
        return self._callback(requests)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.now += seconds


def _copy_manifest_paths(tmp_path: Path) -> dict[str, Any]:
    manifest = json.loads(json.dumps(MANIFEST))
    artifact = tmp_path / manifest["artifact_root"]
    for entry in [*manifest["capability_probes"], *manifest["qualification_commands"]]:
        output = entry["argv"][entry["argv"].index("--output") + 1]
        (tmp_path / output).parent.mkdir(parents=True, exist_ok=True)
    artifact.mkdir(parents=True, exist_ok=True)
    return manifest


def _readiness(tmp_path: Path, *, go: bool = True) -> Path:
    path = tmp_path / ".agent_forge/readiness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": (
                    "go_complete_denominator_capacity"
                    if go
                    else "no_go_current_subscription_window"
                ),
                "gates": {
                    "weekly_covers_complete_campaign": go,
                    "use_balance_off": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return path.relative_to(tmp_path)


def _jsonl(events: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                event,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for event in events
    )


def _completed_pacing_fixture(
    tmp_path: Path,
) -> tuple[
    Path,
    tuple[QualitySelectionV2Slot, ...],
    list[dict[str, Any]],
    int,
    str,
]:
    slots = slots_from_manifest(_copy_manifest_paths(tmp_path), tmp_path)
    events: list[dict[str, Any]] = [
        {
            "schema_version": 1,
            "sequence": sequence,
            "event_type": "sealed_preflight_event",
        }
        for sequence in range(1, 21)
    ]
    events[-1] = {
        "schema_version": 1,
        "sequence": 20,
        "event_type": "pacing_wait",
        "phase": "qualification_to_formal_cooldown",
        "required_seconds": 900,
        "started_monotonic": 100.0,
        "ended_monotonic": 1000.0,
        "elapsed_seconds": 900.0,
        "result": "passed",
    }
    prefix = _jsonl(events)
    sequence = 20
    now = 1000.0
    for slot in slots:
        argv_sha256 = hashlib.sha256(
            json.dumps(slot.argv, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        sequence += 1
        events.append(
            {
                "schema_version": 1,
                "sequence": sequence,
                "event_type": "formal_pacing_wait",
                "slot_id": slot.slot_id,
                "required_seconds": 300,
                "started_monotonic": now,
                "ended_monotonic": now + 300,
                "elapsed_seconds": 300.0,
                "result": "passed",
            }
        )
        now += 300
        sequence += 1
        events.append(
            {
                "schema_version": 1,
                "sequence": sequence,
                "event_type": "formal_provider_command_started",
                "slot_id": slot.slot_id,
                "candidate_id": slot.candidate_id,
                "argv_sha256": argv_sha256,
                "started_monotonic": now,
            }
        )
        sequence += 1
        events.append(
            {
                "schema_version": 1,
                "sequence": sequence,
                "event_type": "formal_provider_command_completed",
                "slot_id": slot.slot_id,
                "candidate_id": slot.candidate_id,
                "argv_sha256": argv_sha256,
                "started_monotonic": now,
                "ended_monotonic": now + 2,
                "elapsed_seconds": 2.0,
                "exit_code": 0,
                "result": "passed",
            }
        )
        now += 2
    path = tmp_path / ".agent_forge/pacing-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_jsonl(events))
    return path, slots, events, len(prefix), hashlib.sha256(prefix).hexdigest()


def test_manifest_maps_to_exact_case_paired_twenty_slot_contract() -> None:
    slots = slots_from_manifest(MANIFEST, ROOT)
    assert [slot.ordinal for slot in slots] == list(range(1, 21))
    assert len({slot.output_root for slot in slots}) == 20
    for first, second in zip(slots[::2], slots[1::2], strict=True):
        assert first.case_id == second.case_id
        assert first.image_tag == second.image_tag
        assert first.candidate_id != second.candidate_id


def test_no_go_readiness_stops_before_image_or_provider(tmp_path: Path) -> None:
    calls: list[str] = []
    preflight = QualitySelectionV2Preflight(
        project_root=tmp_path,
        manifest=_copy_manifest_paths(tmp_path),
        readiness_path=_readiness(tmp_path, go=False),
        source_gate=lambda _: calls.append("source"),
        credential_gate=lambda _: calls.append("credential"),
        image_sealer=FakeImageSealer(lambda _: calls.append("image") or ()),
        run_command=lambda _: calls.append("provider") or 0,
        clock=lambda: 0.0,
        append_event=lambda _: calls.append("ledger"),
        wait=lambda _: calls.append("wait"),
    )

    with pytest.raises(QualitySelectionV2Refused, match="not GO"):
        preflight.qualify()
    assert calls == ["source", "credential"]


def test_qualification_is_ordered_paced_and_returns_sealed_identities(
    tmp_path: Path,
) -> None:
    manifest = _copy_manifest_paths(tmp_path)
    events: list[str] = []
    ledger: list[Mapping[str, Any]] = []
    clock = FakeClock()

    def seal(requests) -> Sequence[Mapping[str, str]]:  # type: ignore[no-untyped-def]
        events.append("seal")
        assert len(requests) == 10
        return [
            {
                "tag": request.tag,
                "repo_digest": f"{request.tag.rsplit(':', 1)[0]}@sha256:{index:064x}",
                "image_id": f"sha256:{index:064x}",
                "platform": request.platform,
            }
            for index, request in enumerate(requests, start=1)
        ]

    def run(argv: Sequence[str]) -> int:
        model = argv[argv.index("--model") + 1]
        events.append(f"run:{model}")
        clock.now += 2
        output = tmp_path / argv[argv.index("--output") + 1]
        output.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        return 0

    preflight = QualitySelectionV2Preflight(
        project_root=tmp_path,
        manifest=manifest,
        readiness_path=_readiness(tmp_path),
        source_gate=lambda _: events.append("source"),
        credential_gate=lambda _: events.append("credential"),
        image_sealer=FakeImageSealer(seal),
        run_command=run,
        clock=clock,
        append_event=ledger.append,
        wait=lambda seconds: (
            events.append(f"wait:{int(seconds)}"),
            clock.wait(seconds),
        ),
    )

    identities = preflight.qualify()

    assert len(identities) == 10
    assert events[:4] == ["source", "credential", "wait:1800", "seal"]
    assert events.count("wait:300") == 6
    assert events.count("wait:1800") == 1
    assert events[-1] == "wait:900"
    assert [event for event in events if event.startswith("run:")] == [
        "run:deepseek-v4-pro",
        "run:glm-5.2",
        "run:deepseek-v4-pro",
        "run:glm-5.2",
        "run:glm-5.2",
        "run:deepseek-v4-pro",
    ]
    assert [event["sequence"] for event in ledger] == list(range(1, 21))
    started_events = [
        event for event in ledger if event["event_type"] == "provider_command_started"
    ]
    provider_events = [
        event for event in ledger if event["event_type"] == "provider_command_completed"
    ]
    assert len(started_events) == len(provider_events) == 6
    assert [event["argv_sha256"] for event in started_events] == [
        event["argv_sha256"] for event in provider_events
    ]
    assert [event["provider_sequence"] for event in provider_events] == list(
        range(1, 7)
    )
    assert [event["candidate_id"] for event in provider_events] == [
        "v4-pro",
        "glm",
        "v4-pro",
        "glm",
        "glm",
        "v4-pro",
    ]
    assert all(
        event["elapsed_seconds"] == 2
        and event["result"] == "passed"
        and len(event["argv_sha256"]) == 64
        and "argv" not in event
        for event in provider_events
    )
    assert [
        event["phase"] for event in ledger if event["event_type"] == "pacing_wait"
    ] == [
        "initial_quiet",
        *["between_provider_commands"] * 6,
        "qualification_to_formal_cooldown",
    ]


def test_early_wait_refuses_before_any_provider_command(tmp_path: Path) -> None:
    clock = FakeClock()
    ledger: list[Mapping[str, Any]] = []
    provider_calls: list[Sequence[str]] = []
    preflight = QualitySelectionV2Preflight(
        project_root=tmp_path,
        manifest=_copy_manifest_paths(tmp_path),
        readiness_path=_readiness(tmp_path),
        source_gate=lambda _: None,
        credential_gate=lambda _: None,
        image_sealer=FakeImageSealer(lambda _: ()),
        run_command=lambda argv: provider_calls.append(argv) or 0,
        clock=clock,
        append_event=ledger.append,
        wait=lambda seconds: clock.wait(seconds - 1),
    )

    with pytest.raises(QualitySelectionV2Refused, match="shorter"):
        preflight.qualify()
    assert provider_calls == []
    assert ledger == [
        {
            "schema_version": 1,
            "sequence": 1,
            "event_type": "pacing_wait",
            "phase": "initial_quiet",
            "required_seconds": 1800,
            "started_monotonic": 0.0,
            "ended_monotonic": 1799.0,
            "elapsed_seconds": 1799.0,
            "result": "failed",
        }
    ]


def test_short_between_command_wait_blocks_the_next_provider(tmp_path: Path) -> None:
    clock = FakeClock()
    ledger: list[Mapping[str, Any]] = []
    provider_calls: list[Sequence[str]] = []

    def wait(seconds: float) -> None:
        clock.wait(seconds if seconds == 1800 else seconds - 1)

    def run(argv: Sequence[str]) -> int:
        provider_calls.append(argv)
        output = tmp_path / argv[argv.index("--output") + 1]
        output.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        return 0

    tags = [item["image"]["tag"] for item in MANIFEST["commands"][::2]]
    preflight = QualitySelectionV2Preflight(
        project_root=tmp_path,
        manifest=_copy_manifest_paths(tmp_path),
        readiness_path=_readiness(tmp_path),
        source_gate=lambda _: None,
        credential_gate=lambda _: None,
        image_sealer=FakeImageSealer(lambda _: tuple({"tag": tag} for tag in tags)),
        run_command=run,
        clock=clock,
        append_event=ledger.append,
        wait=wait,
    )

    with pytest.raises(QualitySelectionV2Refused, match="shorter"):
        preflight.qualify()
    assert len(provider_calls) == 1
    assert [event["event_type"] for event in ledger] == [
        "pacing_wait",
        "provider_command_started",
        "provider_command_completed",
        "pacing_wait",
    ]
    assert ledger[-1]["result"] == "failed"


def test_existing_qualification_output_refuses_without_overwrite(
    tmp_path: Path,
) -> None:
    manifest = _copy_manifest_paths(tmp_path)
    clock = FakeClock()
    output = manifest["capability_probes"][0]["argv"][-1]
    sealed = tmp_path / output
    sealed.write_text("sealed", encoding="utf-8")
    tags = [item["image"]["tag"] for item in manifest["commands"][::2]]
    preflight = QualitySelectionV2Preflight(
        project_root=tmp_path,
        manifest=manifest,
        readiness_path=_readiness(tmp_path),
        source_gate=lambda _: None,
        credential_gate=lambda _: None,
        image_sealer=FakeImageSealer(lambda _: tuple({"tag": tag} for tag in tags)),
        run_command=lambda _: 0,
        clock=clock,
        append_event=lambda _: None,
        wait=clock.wait,
    )

    with pytest.raises(QualitySelectionV2Refused, match="already exists"):
        preflight.qualify()
    assert sealed.read_text(encoding="utf-8") == "sealed"


def test_formal_launcher_enforces_order_wait_and_started_event(tmp_path: Path) -> None:
    slots = slots_from_manifest(_copy_manifest_paths(tmp_path), tmp_path)[:2]
    clock = FakeClock()
    ledger: list[Mapping[str, Any]] = []
    launched: list[Sequence[str]] = []
    launcher = QualitySelectionV2FormalLauncher(
        slots=slots,
        minimum_seconds=300,
        run_command=lambda argv: launched.append(argv) or 0,
        clock=clock,
        append_event=ledger.append,
        initial_sequence=20,
        wait=clock.wait,
    )

    assert launcher(slots[0].argv) == 0
    assert [item["sequence"] for item in ledger] == [21, 22, 23]
    assert [item["event_type"] for item in ledger] == [
        "formal_pacing_wait",
        "formal_provider_command_started",
        "formal_provider_command_completed",
    ]
    assert launched == [slots[0].argv]
    with pytest.raises(QualitySelectionV2Refused, match="order"):
        launcher(slots[0].argv)


def test_formal_short_wait_stops_before_provider_call(tmp_path: Path) -> None:
    slot = slots_from_manifest(_copy_manifest_paths(tmp_path), tmp_path)[0]
    clock = FakeClock()
    launched: list[Sequence[str]] = []
    launcher = QualitySelectionV2FormalLauncher(
        slots=(slot,),
        minimum_seconds=300,
        run_command=lambda argv: launched.append(argv) or 0,
        clock=clock,
        append_event=lambda _: None,
        initial_sequence=0,
        wait=lambda seconds: clock.wait(seconds - 1),
    )

    with pytest.raises(QualitySelectionV2Refused, match="too short"):
        launcher(slot.argv)
    assert launched == []


def test_completed_pacing_audit_is_read_only_and_returns_full_hash(
    tmp_path: Path,
) -> None:
    path, slots, _events, prefix_bytes, prefix_sha256 = _completed_pacing_fixture(
        tmp_path
    )
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    digest = audit_quality_selection_v2_completed_pacing(
        path,
        slots,
        prefix_sha256=prefix_sha256,
        prefix_bytes=prefix_bytes,
    )

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize(
    ("event_index", "field", "value"),
    [
        (20, "event_type", "different"),
        (20, "sequence", 999),
        (20, "slot_id", "slot-999"),
        (20, "required_seconds", 299),
        (20, "elapsed_seconds", 299.0),
        (21, "candidate_id", "different"),
        (21, "argv_sha256", "b" * 64),
        (22, "started_monotonic", -1.0),
        (22, "exit_code", 1),
        (22, "result", "exit_nonzero"),
        (22, "unexpected", "field"),
    ],
)
def test_completed_pacing_audit_refuses_any_formal_suffix_drift(
    tmp_path: Path,
    event_index: int,
    field: str,
    value: object,
) -> None:
    path, slots, events, prefix_bytes, prefix_sha256 = _completed_pacing_fixture(
        tmp_path
    )
    events[event_index][field] = value
    path.write_bytes(_jsonl(events))

    with pytest.raises(QualitySelectionV2Refused):
        audit_quality_selection_v2_completed_pacing(
            path,
            slots,
            prefix_sha256=prefix_sha256,
            prefix_bytes=prefix_bytes,
        )


def test_completed_pacing_audit_connects_cooldown_to_first_formal_wait(
    tmp_path: Path,
) -> None:
    path, slots, events, _prefix_bytes, _prefix_sha256 = _completed_pacing_fixture(
        tmp_path
    )
    events[19]["ended_monotonic"] = 2000.0
    prefix = _jsonl(events[:20])
    path.write_bytes(_jsonl(events))

    with pytest.raises(QualitySelectionV2Refused, match="wait evidence drift"):
        audit_quality_selection_v2_completed_pacing(
            path,
            slots,
            prefix_sha256=hashlib.sha256(prefix).hexdigest(),
            prefix_bytes=len(prefix),
        )


def test_completed_pacing_audit_refuses_prefix_hash_or_boundary_drift(
    tmp_path: Path,
) -> None:
    path, slots, _events, prefix_bytes, prefix_sha256 = _completed_pacing_fixture(
        tmp_path
    )

    with pytest.raises(QualitySelectionV2Refused, match="prefix hash drift"):
        audit_quality_selection_v2_completed_pacing(
            path,
            slots,
            prefix_sha256="0" * 64,
            prefix_bytes=prefix_bytes,
        )
    with pytest.raises(QualitySelectionV2Refused, match="byte boundary drift"):
        audit_quality_selection_v2_completed_pacing(
            path,
            slots,
            prefix_sha256=prefix_sha256,
            prefix_bytes=prefix_bytes - 1,
        )


def test_completed_pacing_audit_refuses_symlink_and_non_jsonl(tmp_path: Path) -> None:
    path, slots, _events, prefix_bytes, prefix_sha256 = _completed_pacing_fixture(
        tmp_path
    )
    link = tmp_path / "linked-ledger.jsonl"
    link.symlink_to(path)

    with pytest.raises(QualitySelectionV2Refused, match="symlink"):
        audit_quality_selection_v2_completed_pacing(
            link,
            slots,
            prefix_sha256=prefix_sha256,
            prefix_bytes=prefix_bytes,
        )

    path.write_bytes(path.read_bytes().removesuffix(b"\n"))
    with pytest.raises(QualitySelectionV2Refused, match="not complete JSONL"):
        audit_quality_selection_v2_completed_pacing(
            path,
            slots,
            prefix_sha256=prefix_sha256,
            prefix_bytes=prefix_bytes,
        )
