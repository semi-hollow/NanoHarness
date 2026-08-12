from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agent_forge.bench.adapters.campaign_files import FileCampaignJournal
from agent_forge.bench.application.campaign_lifecycle import ExactImageIdentity
from agent_forge.bench.application.formal_campaign import (
    FormalCampaignRefused,
    FormalCampaignRunner,
    FormalCampaignSlot,
    audit_completed_formal_campaign,
    build_formal_launch_evidence,
)
from agent_forge.bench.formal_artifacts import FormalRunExpectation, ValidatedFormalRun


TAG = "swebench/sweb.eval.x86_64.demo:latest"
PLATFORM = "linux/amd64"


def _identity() -> ExactImageIdentity:
    digest = hashlib.sha256(b"formal-campaign-image").hexdigest()
    return {
        "tag": TAG,
        "repo_digest": f"swebench/sweb.eval.x86_64.demo@sha256:{digest}",
        "image_id": f"sha256:{digest}",
        "platform": PLATFORM,
    }


class ImageRuntime:
    def __init__(self) -> None:
        self.current: ExactImageIdentity | None = None
        self.pulls = 0
        self.removals = 0

    def inspect(self, tag: str) -> ExactImageIdentity | None:
        assert tag == TAG
        return dict(self.current) if self.current else None

    def pull(self, tag: str, platform: str) -> None:
        assert (tag, platform) == (TAG, PLATFORM)
        self.pulls += 1
        self.current = _identity()

    def remove_exact_tag(self, tag: str) -> None:
        assert tag == TAG
        self.removals += 1
        self.current = None


class SourceReader:
    def __init__(self) -> None:
        self.value: dict[str, Any] = {"revision": "abc", "dirty": False}

    def read(self) -> dict[str, Any]:
        return dict(self.value)


def _expectation(root: Path, slot_id: str) -> FormalRunExpectation:
    output = root / ".agent_forge" / "formal" / slot_id
    return FormalRunExpectation(
        label=slot_id,
        project_root=root,
        artifact_root=root / ".agent_forge",
        output_root=output,
        instance_ids=("demo__repo-1",),
        command_argv=("agent-forge", "bench", "swebench", "--output-root", str(output)),
        expected_source_identity={},
        expected_source_manifest_path=root / "manifest.json",
        frozen_inputs=(),
        observed_model="strong-model",
        skill_name="swebench",
        skill_version="1",
        skill_content_sha256="a" * 64,
    )


def _validated(label: str) -> ValidatedFormalRun:
    return ValidatedFormalRun(
        run_id=f"run-{label}",
        planned=1,
        finalized=1,
        resolved=1,
        unresolved=0,
        decided=1,
        empty=0,
        infrastructure=0,
        failed_tools=0,
        tokens=1,
        cost=Decimal("0"),
        patch_binding_parts=(),
        artifact_sha256={
            "results.json": "a",
            "scorecard.json": "b",
            "predictions.jsonl": "c",
            "official_aggregate.json": "d",
        },
        command_argv_sha256="a" * 64,
        expected_source_identity_sha256="b" * 64,
        frozen_inputs_sha256="c" * 64,
        config_sha256="d" * 64,
    )


def _slot(root: Path, slot_id: str, group: str = "case-01") -> FormalCampaignSlot:
    return FormalCampaignSlot(slot_id, group, _expectation(root, slot_id), _identity())


def _slots(root: Path, count: int = 20) -> tuple[FormalCampaignSlot, ...]:
    return tuple(
        _slot(root, f"slot-{index:03d}", f"case-{(index + 1) // 2:02d}")
        for index in range(1, count + 1)
    )


def _seed_completed_campaign(
    root: Path, slots: tuple[FormalCampaignSlot, ...]
) -> FileCampaignJournal:
    journal = FileCampaignJournal(root)
    state_root = Path(".agent_forge/campaign-state")
    journal.write(
        state_root / "slots.json",
        {
            "campaign_id": "formal-v2",
            "identity_sha256": "a" * 64,
            "slots": {slot.slot_id: "finished:validated" for slot in slots},
        },
    )
    for slot in slots:
        assert journal.create_once(
            state_root / "slots.json.started" / f"{slot.slot_id}.marker"
        )
        journal.write(
            state_root / "launches" / f"{slot.slot_id}.json",
            build_formal_launch_evidence(
                slot, _identity(), {"revision": "abc", "dirty": False}
            ),
        )
        slot.expectation.output_root.mkdir(parents=True)
    return journal


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _runner(
    root: Path,
    runtime: ImageRuntime,
    source: SourceReader,
    launches: list[str],
    validator: Any,
) -> FormalCampaignRunner:
    def launch(argv: Any) -> int:
        slot_id = Path(argv[-1]).name
        launches.append(slot_id)
        (root / ".agent_forge" / "formal" / slot_id).mkdir(parents=True)
        return 0

    return FormalCampaignRunner(
        journal=FileCampaignJournal(root),
        state_root=".agent_forge/campaign-state",
        campaign_id="formal-v2",
        identity_sha256="a" * 64,
        slot_ids=("slot-001", "slot-002"),
        image_runtime=runtime,
        source_reader=source,
        expected_launch_source={"revision": "abc", "dirty": False},
        launch_command=launch,
        validator=validator,
    )


def test_valid_pair_reuses_one_image_lease(tmp_path: Path) -> None:
    runtime = ImageRuntime()
    source = SourceReader()
    launches: list[str] = []
    runner = _runner(
        tmp_path, runtime, source, launches, lambda item: _validated(item.label)
    )

    records = runner.run_group(
        (_slot(tmp_path, "slot-001"), _slot(tmp_path, "slot-002"))
    )

    assert [record.status for record in records] == ["validated", "validated"]
    assert launches == ["slot-001", "slot-002"]
    assert runtime.pulls == 1 and runtime.removals == 1


def test_started_recovery_validates_without_rerun(tmp_path: Path) -> None:
    runtime = ImageRuntime()
    source = SourceReader()
    launches: list[str] = []
    runner = _runner(
        tmp_path, runtime, source, launches, lambda item: _validated(item.label)
    )
    slot = _slot(tmp_path, "slot-001")
    evidence = build_formal_launch_evidence(
        slot, _identity(), {"revision": "abc", "dirty": False}
    )
    journal = FileCampaignJournal(tmp_path)
    journal.write(".agent_forge/campaign-state/launches/slot-001.json", evidence)
    runner._lifecycle.try_start("slot-001")
    slot.expectation.output_root.mkdir(parents=True)

    record = runner.run_group((slot,))[0]

    assert record.status == "validated"
    assert launches == []


def test_started_missing_artifacts_is_interrupted_and_never_rerun(
    tmp_path: Path,
) -> None:
    runtime = ImageRuntime()
    source = SourceReader()
    launches: list[str] = []
    runner = _runner(
        tmp_path, runtime, source, launches, lambda item: _validated(item.label)
    )
    slot = _slot(tmp_path, "slot-001")
    journal = FileCampaignJournal(tmp_path)
    journal.write(
        ".agent_forge/campaign-state/launches/slot-001.json",
        build_formal_launch_evidence(
            slot, _identity(), {"revision": "abc", "dirty": False}
        ),
    )
    runner._lifecycle.try_start("slot-001")

    first = runner.run_group((slot,))[0]
    second = runner.run_group((slot,))[0]

    assert first.status == second.status == "interrupted"
    assert launches == []


def test_source_drift_is_refused_before_marker_or_launch(tmp_path: Path) -> None:
    runtime = ImageRuntime()
    source = SourceReader()
    launches: list[str] = []
    runner = _runner(
        tmp_path, runtime, source, launches, lambda item: _validated(item.label)
    )
    slot = _slot(tmp_path, "slot-001")
    source.value["revision"] = "drifted"

    with pytest.raises(RuntimeError, match="source identity drift"):
        runner.run_group((slot,))
    assert launches == []
    state = FileCampaignJournal(tmp_path).read(".agent_forge/campaign-state/slots.json")
    assert state is not None and state["slots"]["slot-001"] == "pending"


def test_persisted_argv_drift_is_refused_before_marker_or_launch(
    tmp_path: Path,
) -> None:
    runtime = ImageRuntime()
    source = SourceReader()
    launches: list[str] = []
    runner = _runner(
        tmp_path, runtime, source, launches, lambda item: _validated(item.label)
    )
    original = _slot(tmp_path, "slot-001")
    journal = FileCampaignJournal(tmp_path)
    journal.write(
        ".agent_forge/campaign-state/launches/slot-001.json",
        build_formal_launch_evidence(
            original, _identity(), {"revision": "abc", "dirty": False}
        ),
    )
    changed = replace(
        original,
        expectation=replace(
            original.expectation,
            command_argv=(*original.expectation.command_argv, "--changed"),
        ),
    )

    with pytest.raises(RuntimeError, match="launch evidence drift"):
        runner.run_group((changed,))
    assert launches == []


def test_nonzero_launch_and_invalid_artifact_are_terminal(tmp_path: Path) -> None:
    runtime, source = ImageRuntime(), SourceReader()
    launches: list[str] = []
    runner = _runner(
        tmp_path,
        runtime,
        source,
        launches,
        lambda item: (_ for _ in ()).throw(ValueError("bad")),
    )
    record = runner.run_group((_slot(tmp_path, "slot-001"),))[0]

    assert record.status == "invalid"
    assert launches == ["slot-001"]
    runner.run_group((_slot(tmp_path, "slot-001"),))
    assert launches == ["slot-001"]


def test_pair_stops_before_second_slot_after_first_failure(tmp_path: Path) -> None:
    runtime = ImageRuntime()
    source = SourceReader()
    launches: list[str] = []
    runner = _runner(
        tmp_path,
        runtime,
        source,
        launches,
        lambda item: (_ for _ in ()).throw(ValueError("bad")),
    )

    records = runner.run_group(
        (_slot(tmp_path, "slot-001"), _slot(tmp_path, "slot-002"))
    )

    assert [record.status for record in records] == ["invalid"]
    assert launches == ["slot-001"]
    assert runtime.removals == 0


def test_completed_campaign_audit_rechecks_all_twenty_slots_without_writes(
    tmp_path: Path,
) -> None:
    slots = _slots(tmp_path)
    journal = _seed_completed_campaign(tmp_path, slots)
    state_root = tmp_path / ".agent_forge/campaign-state"
    before = _tree_snapshot(state_root)
    validated_labels: list[str] = []

    def validator(expectation: FormalRunExpectation) -> ValidatedFormalRun:
        validated_labels.append(expectation.label)
        return _validated(expectation.label)

    records = audit_completed_formal_campaign(
        journal=journal,
        state_root=".agent_forge/campaign-state",
        campaign_id="formal-v2",
        identity_sha256="a" * 64,
        slots=slots,
        expected_launch_source={"revision": "abc", "dirty": False},
        validator=validator,
    )

    assert len(records) == 20
    assert [record.slot_id for record in records] == [slot.slot_id for slot in slots]
    assert all(record.status == "validated" for record in records)
    assert validated_labels == [slot.slot_id for slot in slots]
    assert _tree_snapshot(state_root) == before


def test_completed_campaign_audit_does_not_recover_pending_marker(
    tmp_path: Path,
) -> None:
    slots = _slots(tmp_path, 1)
    journal = _seed_completed_campaign(tmp_path, slots)
    state_path = tmp_path / ".agent_forge/campaign-state/slots.json"
    state = journal.read(state_path)
    assert state is not None
    state["slots"]["slot-001"] = "pending"
    journal.write(state_path, state)
    before = state_path.read_bytes()

    with pytest.raises(FormalCampaignRefused, match="not completely validated"):
        audit_completed_formal_campaign(
            journal=journal,
            state_root=".agent_forge/campaign-state",
            campaign_id="formal-v2",
            identity_sha256="a" * 64,
            slots=slots,
            expected_launch_source={"revision": "abc", "dirty": False},
            validator=lambda item: _validated(item.label),
        )

    assert state_path.read_bytes() == before
    unchanged = journal.read(state_path)
    assert unchanged is not None
    assert unchanged["slots"]["slot-001"] == "pending"


def test_completed_campaign_audit_requires_every_start_marker(tmp_path: Path) -> None:
    slots = _slots(tmp_path, 2)
    journal = _seed_completed_campaign(tmp_path, slots)
    marker = journal.resolve(
        ".agent_forge/campaign-state/slots.json.started/slot-002.marker"
    )
    marker.unlink()
    validated: list[str] = []

    def validator(expectation: FormalRunExpectation) -> ValidatedFormalRun:
        validated.append(expectation.label)
        return _validated(expectation.label)

    with pytest.raises(FormalCampaignRefused, match="start marker is missing"):
        audit_completed_formal_campaign(
            journal=journal,
            state_root=".agent_forge/campaign-state",
            campaign_id="formal-v2",
            identity_sha256="a" * 64,
            slots=slots,
            expected_launch_source={"revision": "abc", "dirty": False},
            validator=validator,
        )

    assert validated == []


@pytest.mark.parametrize(
    "field",
    ["source_identity", "command_argv_sha256", "image_identity", "expected_output"],
)
def test_completed_campaign_audit_refuses_each_launch_receipt_drift(
    tmp_path: Path, field: str
) -> None:
    slots = _slots(tmp_path, 2)
    journal = _seed_completed_campaign(tmp_path, slots)
    receipt_path = Path(".agent_forge/campaign-state/launches/slot-002.json")
    receipt = journal.read(receipt_path)
    assert receipt is not None
    if field == "source_identity":
        receipt[field] = {"revision": "drifted", "dirty": False}
    elif field == "image_identity":
        receipt[field] = {**receipt[field], "image_id": f"sha256:{'b' * 64}"}
    else:
        receipt[field] = "drifted"
    journal.write(receipt_path, receipt)
    validated: list[str] = []

    def validator(expectation: FormalRunExpectation) -> ValidatedFormalRun:
        validated.append(expectation.label)
        return _validated(expectation.label)

    with pytest.raises(FormalCampaignRefused, match="launch evidence drift"):
        audit_completed_formal_campaign(
            journal=journal,
            state_root=".agent_forge/campaign-state",
            campaign_id="formal-v2",
            identity_sha256="a" * 64,
            slots=slots,
            expected_launch_source={"revision": "abc", "dirty": False},
            validator=validator,
        )

    assert validated == []


def test_completed_campaign_audit_exposes_no_partial_records_on_artifact_failure(
    tmp_path: Path,
) -> None:
    slots = _slots(tmp_path, 3)
    journal = _seed_completed_campaign(tmp_path, slots)
    validated: list[str] = []

    def validator(expectation: FormalRunExpectation) -> ValidatedFormalRun:
        validated.append(expectation.label)
        if expectation.label == "slot-003":
            raise ValueError("artifact drift")
        return _validated(expectation.label)

    with pytest.raises(FormalCampaignRefused, match="artifact audit failed: slot-003"):
        audit_completed_formal_campaign(
            journal=journal,
            state_root=".agent_forge/campaign-state",
            campaign_id="formal-v2",
            identity_sha256="a" * 64,
            slots=slots,
            expected_launch_source={"revision": "abc", "dirty": False},
            validator=validator,
        )

    assert validated == ["slot-001", "slot-002", "slot-003"]
