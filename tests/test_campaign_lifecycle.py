from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

import pytest

from agent_forge.bench.adapters.campaign_files import (
    AppendOnlyJsonlLedger,
    FileCampaignJournal,
)
from agent_forge.bench.application.campaign_lifecycle import (
    ExactImageIdentity,
    ExactImageLease,
    FreeSpaceGuardedExactImageRuntime,
    NoRerunSlotLifecycle,
)


TAG = "swebench/sweb.eval.x86_64.demo:latest"
PLATFORM = "linux/amd64"
IDENTITY_SHA256 = "a" * 64


def _identity(suffix: str = "owned") -> ExactImageIdentity:
    digest = hashlib.sha256(suffix.encode()).hexdigest()
    return {
        "tag": TAG,
        "repo_digest": f"swebench/sweb.eval.x86_64.demo@sha256:{digest}",
        "image_id": f"sha256:{digest}",
        "platform": PLATFORM,
    }


class FakeImageRuntime:
    def __init__(self, current: ExactImageIdentity | None = None) -> None:
        self.current = current
        self.pull_result = _identity()
        self.pulls: list[tuple[str, str]] = []
        self.removals: list[str] = []
        self.on_pull: Callable[[], None] | None = None
        self.raise_during_pull = False
        self.raise_after_remove = False

    def inspect(self, tag: str) -> ExactImageIdentity | None:
        assert tag == TAG
        return dict(self.current) if self.current is not None else None

    def pull(self, tag: str, platform: str) -> None:
        self.pulls.append((tag, platform))
        if self.on_pull is not None:
            self.on_pull()
        self.current = dict(self.pull_result)
        if self.raise_during_pull:
            raise RuntimeError("simulated pull crash")

    def remove_exact_tag(self, tag: str) -> None:
        self.removals.append(tag)
        self.current = None
        if self.raise_after_remove:
            raise RuntimeError("simulated remove crash")


class CrashAfterMarkerJournal(FileCampaignJournal):
    def __init__(self, project_dir: Path) -> None:
        super().__init__(project_dir)
        self.crash_next_write = False

    def create_once(self, path: str | Path) -> bool:
        created = super().create_once(path)
        self.crash_next_write = created
        return created

    def write(self, path: str | Path, payload: dict[str, object]) -> None:
        if self.crash_next_write:
            self.crash_next_write = False
            raise RuntimeError("simulated state-write crash")
        super().write(path, payload)


def test_free_space_guard_blocks_pull_without_touching_runtime() -> None:
    runtime = FakeImageRuntime()
    guarded = FreeSpaceGuardedExactImageRuntime(
        runtime,
        minimum_free_bytes=100,
        free_space_probe=lambda: 99,
    )

    with pytest.raises(RuntimeError, match="insufficient free space"):
        guarded.pull(TAG, PLATFORM)
    assert runtime.pulls == []


def test_free_space_guard_delegates_pull_at_threshold() -> None:
    runtime = FakeImageRuntime()
    guarded = FreeSpaceGuardedExactImageRuntime(
        runtime,
        minimum_free_bytes=100,
        free_space_probe=lambda: 100,
    )

    guarded.pull(TAG, PLATFORM)
    assert runtime.pulls == [(TAG, PLATFORM)]


def test_free_space_guard_does_not_probe_for_inspect_or_remove() -> None:
    runtime = FakeImageRuntime(_identity("preexisting"))
    calls = 0

    def probe() -> int:
        nonlocal calls
        calls += 1
        return 100

    guarded = FreeSpaceGuardedExactImageRuntime(
        runtime,
        minimum_free_bytes=100,
        free_space_probe=probe,
    )

    assert guarded.inspect(TAG) == _identity("preexisting")
    guarded.remove_exact_tag(TAG)
    assert calls == 0
    assert runtime.removals == [TAG]


def test_preexisting_exact_image_lease_does_not_run_free_space_probe(
    tmp_path: Path,
) -> None:
    runtime = FakeImageRuntime(_identity("preexisting"))

    def unexpected_probe() -> int:
        raise AssertionError("preexisting exact image must not require a pull gate")

    lease = ExactImageLease(
        FileCampaignJournal(tmp_path),
        "leases/preexisting-guarded.json",
        FreeSpaceGuardedExactImageRuntime(
            runtime,
            minimum_free_bytes=100,
            free_space_probe=unexpected_probe,
        ),
        expected_identity=_identity("preexisting"),
    )

    assert lease.acquire() == _identity("preexisting")
    assert runtime.pulls == []


def test_started_slot_is_never_rerun_after_recovery(tmp_path: Path) -> None:
    journal = FileCampaignJournal(tmp_path)
    state_path = ".agent_forge/formal/campaign.json"
    lifecycle = NoRerunSlotLifecycle(
        journal,
        state_path,
        campaign_id="formal-v1",
        identity_sha256=IDENTITY_SHA256,
        slot_ids=("slot-001", "slot-002"),
    )

    assert lifecycle.try_start("slot-001") is True
    recovered = NoRerunSlotLifecycle(
        journal,
        state_path,
        campaign_id="formal-v1",
        identity_sha256=IDENTITY_SHA256,
        slot_ids=("slot-001", "slot-002"),
    )
    assert recovered.try_start("slot-001") is False
    recovered.finish("slot-001", "failed-provider")
    assert recovered.try_start("slot-001") is False
    assert recovered.try_start("slot-002") is True


def test_marker_recovers_crash_before_started_state_write(tmp_path: Path) -> None:
    state_path = ".agent_forge/formal/campaign.json"
    crashing = CrashAfterMarkerJournal(tmp_path)
    lifecycle = NoRerunSlotLifecycle(
        crashing,
        state_path,
        campaign_id="formal-v1",
        identity_sha256=IDENTITY_SHA256,
        slot_ids=("slot-001",),
    )
    with pytest.raises(RuntimeError, match="state-write crash"):
        lifecycle.try_start("slot-001")

    journal = FileCampaignJournal(tmp_path)
    recovered = NoRerunSlotLifecycle(
        journal,
        state_path,
        campaign_id="formal-v1",
        identity_sha256=IDENTITY_SHA256,
        slot_ids=("slot-001",),
    )
    assert recovered.try_start("slot-001") is False
    state = journal.read(state_path)
    assert state is not None and state["slots"] == {"slot-001": "started"}


def test_slot_lifecycle_rejects_frozen_campaign_identity_drift(tmp_path: Path) -> None:
    journal = FileCampaignJournal(tmp_path)
    state_path = ".agent_forge/formal/campaign.json"
    NoRerunSlotLifecycle(
        journal,
        state_path,
        campaign_id="formal-v1",
        identity_sha256=IDENTITY_SHA256,
        slot_ids=("slot-001",),
    )

    with pytest.raises(ValueError, match="identity changed"):
        NoRerunSlotLifecycle(
            journal,
            state_path,
            campaign_id="formal-v1",
            identity_sha256="b" * 64,
            slot_ids=("slot-001",),
        )


def test_campaign_journal_rejects_parent_and_symlink_escape(tmp_path: Path) -> None:
    journal = FileCampaignJournal(tmp_path)
    with pytest.raises(ValueError, match="cannot contain"):
        journal.resolve(".agent_forge/../escape.json")

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        journal.resolve("linked/state.json")

    outside_state = outside / "state.json"
    outside_state.write_text("untouched", encoding="utf-8")
    (tmp_path / "state.json.tmp").symlink_to(outside_state)
    with pytest.raises(ValueError, match="temporary state path"):
        journal.write("state.json", {"safe": True})
    assert outside_state.read_text(encoding="utf-8") == "untouched"


def test_append_only_ledger_claims_phase_and_fsyncs_strict_sequence(
    tmp_path: Path,
) -> None:
    relative = ".agent_forge/formal/pacing.jsonl"
    ledger = AppendOnlyJsonlLedger(tmp_path, relative)
    ledger.append({"sequence": 1, "event_type": "claimed"})
    ledger.append({"sequence": 2, "event_type": "completed", "ok": True})

    assert (tmp_path / relative).read_text(encoding="utf-8").splitlines() == [
        '{"event_type":"claimed","sequence":1}',
        '{"event_type":"completed","ok":true,"sequence":2}',
    ]
    with pytest.raises(FileExistsError, match="cannot be rerun"):
        AppendOnlyJsonlLedger(tmp_path, relative)
    with pytest.raises(ValueError, match="sequence drift"):
        ledger.append({"sequence": 4, "event_type": "out-of-order"})


def test_pull_intent_precedes_pull_and_records_frozen_expected_identity(
    tmp_path: Path,
) -> None:
    journal = FileCampaignJournal(tmp_path)
    state_path = ".agent_forge/formal/images/demo.json"
    runtime = FakeImageRuntime()
    pulled_identity = _identity("registry-observed")
    runtime.pull_result = pulled_identity

    def assert_intent() -> None:
        state = journal.read(state_path)
        assert state is not None
        assert state["phase"] == "pull_intent"
        assert state["preexisting"] is False
        assert state["identity"] == pulled_identity

    runtime.on_pull = assert_intent
    lease = ExactImageLease(
        journal,
        state_path,
        runtime,
        expected_identity=pulled_identity,
    )

    # The caller supplies the separately frozen manifest/registry identity.
    assert lease.acquire() == pulled_identity
    assert runtime.pulls == [(TAG, PLATFORM)]
    state = journal.read(state_path)
    assert state is not None and state["identity"] == pulled_identity


def test_preexisting_image_is_never_removed(tmp_path: Path) -> None:
    journal = FileCampaignJournal(tmp_path)
    runtime = FakeImageRuntime(_identity("preexisting"))
    lease = ExactImageLease(
        journal,
        "leases/preexisting.json",
        runtime,
        expected_identity=_identity("preexisting"),
    )

    assert lease.acquire() == _identity("preexisting")
    assert lease.release() is False
    assert runtime.removals == []
    state = journal.read("leases/preexisting.json")
    assert state is not None and state["preexisting"] is True
    assert state["phase"] == "released"


def test_pull_rejects_digest_different_from_frozen_expected_identity(
    tmp_path: Path,
) -> None:
    journal = FileCampaignJournal(tmp_path)
    runtime = FakeImageRuntime()
    runtime.pull_result = _identity("registry-drift")
    state_path = "leases/digest-drift.json"
    lease = ExactImageLease(
        journal,
        state_path,
        runtime,
        expected_identity=_identity("frozen-manifest"),
    )

    with pytest.raises(RuntimeError, match="frozen expected identity"):
        lease.acquire()
    assert runtime.removals == []
    state = journal.read(state_path)
    assert state is not None and state["phase"] == "retained_identity_mismatch"


def test_retagged_owned_image_is_retained(tmp_path: Path) -> None:
    journal = FileCampaignJournal(tmp_path)
    runtime = FakeImageRuntime()
    lease = ExactImageLease(
        journal,
        "leases/retag.json",
        runtime,
        expected_identity=_identity(),
    )
    lease.acquire()
    runtime.current = _identity("retagged")

    assert lease.release() is False
    assert runtime.removals == []
    state = journal.read("leases/retag.json")
    assert state is not None and state["phase"] == "retained_identity_mismatch"


def test_exact_owned_tag_is_removed_without_broad_prune(tmp_path: Path) -> None:
    journal = FileCampaignJournal(tmp_path)
    runtime = FakeImageRuntime()
    lease = ExactImageLease(
        journal,
        "leases/owned.json",
        runtime,
        expected_identity=_identity(),
    )
    lease.acquire()

    assert lease.release() is True
    assert runtime.removals == [TAG]
    assert all("prune" not in command for command in runtime.removals)
    state = journal.read("leases/owned.json")
    assert state is not None and state["phase"] == "released"


def test_release_recovers_after_remove_crash_without_second_delete(
    tmp_path: Path,
) -> None:
    journal = FileCampaignJournal(tmp_path)
    runtime = FakeImageRuntime()
    state_path = "leases/remove-crash.json"
    lease = ExactImageLease(
        journal,
        state_path,
        runtime,
        expected_identity=_identity(),
    )
    lease.acquire()
    runtime.raise_after_remove = True
    with pytest.raises(RuntimeError, match="remove crash"):
        lease.release()
    assert journal.read(state_path)["phase"] == "release_intent"  # type: ignore[index]

    runtime.raise_after_remove = False
    recovered = ExactImageLease(
        journal,
        state_path,
        runtime,
        expected_identity=_identity(),
    )
    assert recovered.release() is False
    assert runtime.removals == [TAG]


def test_post_pull_crash_recovery_never_claims_image_ownership(tmp_path: Path) -> None:
    journal = FileCampaignJournal(tmp_path)
    runtime = FakeImageRuntime()
    runtime.raise_during_pull = True
    state_path = "leases/pull-crash.json"
    lease = ExactImageLease(
        journal,
        state_path,
        runtime,
        expected_identity=_identity(),
    )
    with pytest.raises(RuntimeError, match="pull crash"):
        lease.acquire()

    recovered = ExactImageLease(
        journal,
        state_path,
        runtime,
        expected_identity=_identity(),
    )
    assert recovered.acquire() == _identity()
    assert recovered.release() is False
    assert runtime.removals == []
    state = journal.read(state_path)
    assert state is not None and state["phase"] == "released"
    assert state["preexisting"] is True


def test_image_coordinates_and_all_exact_identity_fields_are_required(
    tmp_path: Path,
) -> None:
    journal = FileCampaignJournal(tmp_path)
    with pytest.raises(ValueError, match="traversal-free"):
        ExactImageLease(
            journal,
            "leases/invalid.json",
            FakeImageRuntime(),
            expected_identity={
                **_identity(),
                "tag": "../escape:latest",
            },
        )

    wrong_platform = _identity()
    wrong_platform["platform"] = "linux/arm64"
    lease = ExactImageLease(
        journal,
        "leases/wrong-platform.json",
        FakeImageRuntime(wrong_platform),
        expected_identity=_identity(),
    )
    with pytest.raises(ValueError, match="exactly match"):
        lease.acquire()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_id", "sha256:owned"),
        ("image_id", f"sha256:{'A' * 64}"),
        (
            "repo_digest",
            "swebench/sweb.eval.x86_64.demo@sha256:owned",
        ),
        (
            "repo_digest",
            f"swebench/sweb.eval.x86_64.demo@sha256:{'A' * 64}",
        ),
    ],
)
def test_image_identity_requires_exact_lowercase_sha256(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    identity = _identity()
    identity[field] = value
    with pytest.raises(ValueError, match="exactly match"):
        ExactImageLease(
            FileCampaignJournal(tmp_path),
            f"leases/{field}.json",
            FakeImageRuntime(),
            expected_identity=identity,
        )


def test_campaign_journal_write_once_preserves_first_complete_payload(
    tmp_path: Path,
) -> None:
    journal = FileCampaignJournal(tmp_path)
    path = ".agent_forge/formal/summary.json"

    assert journal.write_once(path, {"status": "winner", "resolved": 8}) is True
    assert journal.write_once(path, {"status": "replacement"}) is False

    assert journal.read(path) == {"status": "winner", "resolved": 8}
    assert list((tmp_path / ".agent_forge/formal").glob(".*.tmp")) == []


def test_campaign_journal_write_once_refuses_symlink_destination(
    tmp_path: Path,
) -> None:
    journal = FileCampaignJournal(tmp_path)
    destination = tmp_path / ".agent_forge/formal/summary.json"
    destination.parent.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text('{"status":"untouched"}\n', encoding="utf-8")
    destination.symlink_to(target)

    assert journal.write_once(destination, {"status": "winner"}) is False
    assert target.read_text(encoding="utf-8") == '{"status":"untouched"}\n'


def test_campaign_journal_write_once_link_failure_leaves_no_final_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = FileCampaignJournal(tmp_path)
    destination = tmp_path / ".agent_forge/formal/summary.json"

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr("agent_forge.bench.adapters.campaign_files.os.link", fail_link)
    with pytest.raises(OSError, match="simulated publish failure"):
        journal.write_once(destination, {"status": "winner"})

    assert not destination.exists()
    assert list(destination.parent.glob(".*.tmp")) == []
