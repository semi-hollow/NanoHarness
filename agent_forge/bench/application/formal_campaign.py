"""共享 formal campaign 执行器：一次只处理一个 evaluator image 组。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, TypeAlias

from agent_forge.bench.application.campaign_lifecycle import (
    ExactImageIdentity,
    ExactImageLease,
    ExactImageRuntimePort,
    NoRerunSlotLifecycle,
    normalize_exact_image_identity,
)
from agent_forge.bench.formal_artifacts import (
    FormalRunExpectation,
    ValidatedFormalRun,
    validate_formal_run,
)
from agent_forge.bench.ports.campaign import CampaignJournalPort, SourceIdentityPort


class FormalCampaignRefused(RuntimeError):
    """执行前证据与冻结计划冲突，不允许启动 slot。"""


@dataclass(frozen=True)
class FormalCampaignSlot:
    slot_id: str
    lease_group: str
    expectation: FormalRunExpectation
    expected_image_identity: Mapping[str, str]


FormalLaunchEvidence: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class FormalCampaignRecord:
    slot_id: str
    status: str
    return_code: int | None
    validated: ValidatedFormalRun | None
    detail: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in {"validated", "launch_failed", "interrupted", "invalid"}


class FormalCampaignRunner:
    """将 launch ledger、no-rerun marker、image lease 与共享验证器组合。"""

    def __init__(
        self,
        *,
        journal: CampaignJournalPort,
        state_root: str | Path,
        campaign_id: str,
        identity_sha256: str,
        slot_ids: tuple[str, ...],
        image_runtime: ExactImageRuntimePort,
        source_reader: SourceIdentityPort,
        expected_launch_source: Mapping[str, object],
        launch_command: Callable[[Sequence[str]], int],
        validator: Callable[[FormalRunExpectation], ValidatedFormalRun] = (
            validate_formal_run
        ),
    ) -> None:
        self._journal = journal
        self._root = journal.resolve(state_root)
        self._runtime = image_runtime
        self._source = source_reader
        self._expected_source = dict(expected_launch_source)
        self._launch = launch_command
        self._validate = validator
        self._lifecycle = NoRerunSlotLifecycle(
            journal,
            self._root / "slots.json",
            campaign_id=campaign_id,
            identity_sha256=identity_sha256,
            slot_ids=slot_ids,
        )

    def run_group(
        self, slots: tuple[FormalCampaignSlot, ...]
    ) -> tuple[FormalCampaignRecord, ...]:
        """执行一个相邻 image 组；调用方根据返回值决定是否继续。"""

        self._check_group(slots)
        statuses = {
            slot.slot_id: self._lifecycle.status(slot.slot_id) for slot in slots
        }
        for slot in slots:
            if (
                statuses[slot.slot_id] == "pending"
                and slot.expectation.output_root.exists()
            ):
                raise FormalCampaignRefused(
                    f"pending slot output already exists: {slot.slot_id}"
                )
        first = slots[0]
        lease: ExactImageLease | None = None
        image_identity = dict(first.expected_image_identity)
        if any(not status.startswith("finished:") for status in statuses.values()):
            lease = ExactImageLease(
                self._journal,
                self._root / "leases" / f"{first.lease_group}.json",
                self._runtime,
                expected_identity=image_identity,
            )
            image_identity = lease.acquire()
        records: list[FormalCampaignRecord] = []
        for slot in slots:
            record = self._run_slot(slot, image_identity)
            records.append(record)
            if record.status != "validated":
                break
        if (
            lease is not None
            and len(records) == len(slots)
            and all(record.terminal for record in records)
        ):
            lease.release()
        return tuple(records)

    def _run_slot(
        self, slot: FormalCampaignSlot, image_identity: ExactImageIdentity
    ) -> FormalCampaignRecord:
        status = self._lifecycle.status(slot.slot_id)
        current_evidence = self._launch_evidence(slot, image_identity)
        evidence_path = self._root / "launches" / f"{slot.slot_id}.json"
        persisted = self._journal.read(evidence_path)
        if persisted is None and status == "pending":
            self._journal.write(evidence_path, current_evidence)
            persisted = self._journal.read(evidence_path)
        if persisted != current_evidence:
            if status == "pending":
                raise FormalCampaignRefused(
                    f"pending launch evidence drift: {slot.slot_id}"
                )
            if status == "started":
                self._lifecycle.finish(slot.slot_id, "invalid-launch-evidence")
            return FormalCampaignRecord(
                slot.slot_id, "invalid", None, None, "launch evidence drift"
            )
        if status != "pending":
            return self._recover(slot, status)
        if not self._lifecycle.try_start(slot.slot_id):
            return self._recover(slot, self._lifecycle.status(slot.slot_id))
        try:
            return_code = self._launch(slot.expectation.command_argv)
        except Exception as exc:  # noqa: BLE001 - a started slot is terminal on any launcher failure
            self._lifecycle.finish(slot.slot_id, "launch-failed")
            return FormalCampaignRecord(
                slot.slot_id, "launch_failed", None, None, type(exc).__name__
            )
        if return_code != 0:
            self._lifecycle.finish(slot.slot_id, "launch-failed")
            return FormalCampaignRecord(
                slot.slot_id, "launch_failed", return_code, None, "nonzero exit"
            )
        return self._validate_started(slot)

    def _recover(
        self, slot: FormalCampaignSlot, lifecycle_status: str
    ) -> FormalCampaignRecord:
        if lifecycle_status == "started":
            if not slot.expectation.output_root.is_dir():
                self._lifecycle.finish(slot.slot_id, "interrupted")
                return FormalCampaignRecord(
                    slot.slot_id, "interrupted", None, None, "output missing"
                )
            return self._validate_started(slot)
        if not lifecycle_status.startswith("finished:"):
            raise FormalCampaignRefused(f"invalid slot state: {lifecycle_status}")
        prior = lifecycle_status.removeprefix("finished:")
        if prior == "interrupted" and not slot.expectation.output_root.exists():
            return FormalCampaignRecord(
                slot.slot_id, "interrupted", None, None, "output missing"
            )
        try:
            validated = self._validate(slot.expectation)
        except Exception as exc:  # noqa: BLE001 - finished evidence is always rechecked
            mapped = "launch_failed" if prior == "launch-failed" else "invalid"
            if prior == "interrupted" and not slot.expectation.output_root.exists():
                mapped = "interrupted"
            return FormalCampaignRecord(
                slot.slot_id, mapped, None, None, type(exc).__name__
            )
        if prior == "validated":
            return FormalCampaignRecord(slot.slot_id, "validated", 0, validated)
        return FormalCampaignRecord(
            slot.slot_id,
            "launch_failed" if prior == "launch-failed" else "invalid",
            None,
            None,
            f"terminal outcome retained: {prior}",
        )

    def _validate_started(self, slot: FormalCampaignSlot) -> FormalCampaignRecord:
        try:
            validated = self._validate(slot.expectation)
        except Exception as exc:  # noqa: BLE001 - validation failure consumes the formal slot
            self._lifecycle.finish(slot.slot_id, "invalid-artifacts")
            return FormalCampaignRecord(
                slot.slot_id, "invalid", 0, None, type(exc).__name__
            )
        self._lifecycle.finish(slot.slot_id, "validated")
        return FormalCampaignRecord(slot.slot_id, "validated", 0, validated)

    def _launch_evidence(
        self, slot: FormalCampaignSlot, image_identity: ExactImageIdentity
    ) -> FormalLaunchEvidence:
        source = dict(self._source.read())
        if source != self._expected_source:
            raise FormalCampaignRefused("actual launch source identity drift")
        return build_formal_launch_evidence(slot, image_identity, source)

    @staticmethod
    def _check_group(slots: tuple[FormalCampaignSlot, ...]) -> None:
        if not 1 <= len(slots) <= 2:
            raise FormalCampaignRefused("an image group must contain one or two slots")
        first = slots[0]
        if any(
            slot.lease_group != first.lease_group
            or dict(slot.expected_image_identity) != dict(first.expected_image_identity)
            for slot in slots
        ):
            raise FormalCampaignRefused("image group identity drift")
        if len({slot.slot_id for slot in slots}) != len(slots):
            raise FormalCampaignRefused("image group contains duplicate slots")


def build_formal_launch_evidence(
    slot: FormalCampaignSlot,
    image_identity: Mapping[str, str],
    source_identity: Mapping[str, object],
) -> FormalLaunchEvidence:
    """纯函数复算 runner 与审计器共享的启动收据。"""

    source = _canonical_json_object(source_identity, "launch source identity")
    try:
        image = normalize_exact_image_identity(
            str(image_identity.get("tag") or ""),
            str(image_identity.get("platform") or ""),
            image_identity,
        )
    except ValueError as exc:
        raise FormalCampaignRefused("launch image identity is invalid") from exc
    return {
        "schema_version": 1,
        "slot_id": slot.slot_id,
        "lease_group": slot.lease_group,
        "source_identity": source,
        "command_argv_sha256": _json_sha256(list(slot.expectation.command_argv)),
        "image_identity": image,
        "expected_output": str(slot.expectation.output_root.resolve()),
    }


def audit_completed_formal_campaign(
    *,
    journal: CampaignJournalPort,
    state_root: str | Path,
    campaign_id: str,
    identity_sha256: str,
    slots: tuple[FormalCampaignSlot, ...],
    expected_launch_source: Mapping[str, object],
    validator: Callable[[FormalRunExpectation], ValidatedFormalRun] = (
        validate_formal_run
    ),
) -> tuple[FormalCampaignRecord, ...]:
    """只读重验完整 campaign；任何 lifecycle、收据或产物漂移均拒绝。"""

    _check_audit_coordinates(campaign_id, identity_sha256, slots)
    root = journal.resolve(state_root)
    state_path = root / "slots.json"
    state = journal.read(state_path)
    slot_ids = tuple(slot.slot_id for slot in slots)
    if state is None or set(state) != {"campaign_id", "identity_sha256", "slots"}:
        raise FormalCampaignRefused("formal campaign lifecycle state is missing")
    if (
        state.get("campaign_id") != campaign_id
        or state.get("identity_sha256") != identity_sha256
    ):
        raise FormalCampaignRefused("formal campaign lifecycle identity drift")
    lifecycle_slots = state.get("slots")
    if not isinstance(lifecycle_slots, dict) or set(lifecycle_slots) != set(slot_ids):
        raise FormalCampaignRefused("formal campaign lifecycle slots are incomplete")
    if any(
        lifecycle_slots.get(slot_id) != "finished:validated" for slot_id in slot_ids
    ):
        raise FormalCampaignRefused("formal campaign is not completely validated")

    expected_source = _canonical_json_object(
        expected_launch_source, "expected launch source identity"
    )
    marker_root = state_path.with_name(f"{state_path.name}.started")
    for slot in slots:
        marker_path = journal.resolve(marker_root / f"{slot.slot_id}.marker")
        if (
            marker_path.is_symlink()
            or not marker_path.is_file()
            or marker_path.read_bytes() != b"started\n"
        ):
            raise FormalCampaignRefused(
                f"formal campaign start marker is missing: {slot.slot_id}"
            )
        expected_receipt = build_formal_launch_evidence(
            slot, slot.expected_image_identity, expected_source
        )
        receipt = journal.read(root / "launches" / f"{slot.slot_id}.json")
        if receipt != expected_receipt:
            raise FormalCampaignRefused(
                f"formal campaign launch evidence drift: {slot.slot_id}"
            )

    validated_runs: list[ValidatedFormalRun] = []
    for slot in slots:
        try:
            validated = validator(slot.expectation)
        except Exception as exc:  # noqa: BLE001 - 审计必须把任意产物异常转成统一拒绝
            raise FormalCampaignRefused(
                f"formal campaign artifact audit failed: {slot.slot_id}"
            ) from exc
        if not isinstance(validated, ValidatedFormalRun):
            raise FormalCampaignRefused(
                f"formal campaign validator returned invalid evidence: {slot.slot_id}"
            )
        validated_runs.append(validated)
    return tuple(
        FormalCampaignRecord(slot.slot_id, "validated", 0, validated)
        for slot, validated in zip(slots, validated_runs, strict=True)
    )


def _check_audit_coordinates(
    campaign_id: str,
    identity_sha256: str,
    slots: tuple[FormalCampaignSlot, ...],
) -> None:
    safe_token = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
    sha256 = re.compile(r"^[0-9a-f]{64}$")
    slot_ids = tuple(slot.slot_id for slot in slots)
    if not safe_token.fullmatch(campaign_id):
        raise FormalCampaignRefused("formal campaign id is invalid")
    if not sha256.fullmatch(identity_sha256):
        raise FormalCampaignRefused("formal campaign identity is invalid")
    if (
        not slot_ids
        or len(slot_ids) != len(set(slot_ids))
        or any(not safe_token.fullmatch(slot_id) for slot_id in slot_ids)
    ):
        raise FormalCampaignRefused("formal campaign slots are invalid")


def _canonical_json_object(
    value: Mapping[str, object], label: str
) -> dict[str, object]:
    candidate = dict(value)
    if any(not isinstance(key, str) for key in candidate):
        raise FormalCampaignRefused(f"{label} is not a JSON object")
    try:
        encoded = json.dumps(
            candidate,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise FormalCampaignRefused(f"{label} is not JSON") from exc
    if not isinstance(decoded, dict) or decoded != candidate:
        raise FormalCampaignRefused(f"{label} is not canonical JSON")
    return candidate


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "FormalCampaignRecord",
    "FormalCampaignRefused",
    "FormalCampaignRunner",
    "FormalCampaignSlot",
    "FormalLaunchEvidence",
    "audit_completed_formal_campaign",
    "build_formal_launch_evidence",
]
