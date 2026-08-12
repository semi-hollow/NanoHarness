from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent_forge.bench.ports.campaign import CampaignJournalPort

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


ExactImageIdentity = dict[str, str]


class ExactImageRuntimePort(Protocol):
    def inspect(self, tag: str) -> ExactImageIdentity | None: ...
    def pull(self, tag: str, platform: str) -> None: ...
    def remove_exact_tag(self, tag: str) -> None: ...


class FreeSpaceGuardedExactImageRuntime(ExactImageRuntimePort):
    """在每次实际 pull 前执行可注入的 Docker-data 空间门禁。"""

    def __init__(
        self,
        runtime: ExactImageRuntimePort,
        *,
        minimum_free_bytes: int,
        free_space_probe: Callable[[], int],
    ) -> None:
        if type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must be a non-negative integer")
        self._runtime = runtime
        self._minimum_free_bytes = minimum_free_bytes
        self._free_space_probe = free_space_probe

    def inspect(self, tag: str) -> ExactImageIdentity | None:
        return self._runtime.inspect(tag)

    def pull(self, tag: str, platform: str) -> None:
        free_bytes = self._free_space_probe()
        if type(free_bytes) is not int or free_bytes < 0:
            raise RuntimeError("free-space probe returned an invalid byte count")
        if free_bytes < self._minimum_free_bytes:
            raise RuntimeError("insufficient free space for exact image pull")
        self._runtime.pull(tag, platform)

    def remove_exact_tag(self, tag: str) -> None:
        self._runtime.remove_exact_tag(tag)


class NoRerunSlotLifecycle:
    def __init__(
        self,
        journal: CampaignJournalPort,
        state_path: str | Path,
        *,
        campaign_id: str,
        identity_sha256: str,
        slot_ids: tuple[str, ...],
    ) -> None:
        _safe_token(campaign_id, "campaign_id")
        if not _SHA256.fullmatch(identity_sha256):
            raise ValueError("identity_sha256 must be a lowercase SHA-256 digest")
        if not slot_ids or len(slot_ids) != len(set(slot_ids)):
            raise ValueError("slot_ids must be non-empty and unique")
        for slot_id in slot_ids:
            _safe_token(slot_id, "slot_id")
        self._journal = journal
        self._path = journal.resolve(state_path)
        self._campaign_id = campaign_id
        self._identity_sha256 = identity_sha256
        self._slot_ids = slot_ids
        self._load()

    def try_start(self, slot_id: str) -> bool:
        _safe_token(slot_id, "slot_id")
        state = self._load()
        slots = state["slots"]
        if slots.get(slot_id) != "pending":
            if slot_id not in slots:
                raise ValueError(f"unknown campaign slot: {slot_id}")
            return False
        if not self._journal.create_once(self._marker(slot_id)):
            self._load()
            return False
        slots[slot_id] = "started"
        self._journal.write(self._path, state)
        return True

    def status(self, slot_id: str) -> str:
        """读取已恢复 marker 后的单调 slot 状态。"""

        _safe_token(slot_id, "slot_id")
        slots = self._load()["slots"]
        if slot_id not in slots:
            raise ValueError(f"unknown campaign slot: {slot_id}")
        return str(slots[slot_id])

    def finish(self, slot_id: str, outcome: str) -> None:
        _safe_token(slot_id, "slot_id")
        _safe_token(outcome, "outcome")
        state = self._load()
        slots = state["slots"]
        if slot_id not in slots:
            raise ValueError(f"unknown campaign slot: {slot_id}")
        status = str(slots[slot_id])
        finished = f"finished:{outcome}"
        if status == "pending":
            raise ValueError(f"slot was never started: {slot_id}")
        if status.startswith("finished:"):
            if status == finished:
                return
            raise ValueError(f"slot already finished: {slot_id}")
        slots[slot_id] = finished
        self._journal.write(self._path, state)

    def _load(self) -> dict[str, Any]:
        state = self._journal.read(self._path)
        if state is None:
            state = {
                "campaign_id": self._campaign_id,
                "identity_sha256": self._identity_sha256,
                "slots": {slot_id: "pending" for slot_id in self._slot_ids},
            }
            self._journal.write(self._path, state)
            return state
        if (
            state.get("campaign_id") != self._campaign_id
            or state.get("identity_sha256") != self._identity_sha256
        ):
            raise ValueError("slot lifecycle identity changed; use a new state path")
        slots = state.get("slots")
        if not isinstance(slots, dict):
            raise ValueError("slot lifecycle state is incomplete")
        if set(slots) != set(self._slot_ids):
            raise ValueError("slot lifecycle state is incomplete")
        recovered = False
        for slot_id in self._slot_ids:
            marker_exists = self._journal.resolve(self._marker(slot_id)).is_file()
            if slots[slot_id] == "pending" and marker_exists:
                slots[slot_id] = "started"
                recovered = True
        if recovered:
            self._journal.write(self._path, state)
        return state

    def _marker(self, slot_id: str) -> Path:
        directory = self._path.with_name(f"{self._path.name}.started")
        return directory / f"{slot_id}.marker"


class ExactImageLease:
    def __init__(
        self,
        journal: CampaignJournalPort,
        state_path: str | Path,
        runtime: ExactImageRuntimePort,
        *,
        expected_identity: ExactImageIdentity,
    ) -> None:
        tag = str(expected_identity.get("tag") or "")
        platform = str(expected_identity.get("platform") or "")
        validate_exact_image_coordinates(tag, platform)
        self._journal = journal
        self._path = journal.resolve(state_path)
        self._runtime = runtime
        self._tag = tag
        self._platform = platform
        self._expected = normalize_exact_image_identity(
            self._tag,
            self._platform,
            expected_identity,
        )

    def acquire(self) -> ExactImageIdentity:
        state = self._load()
        if state is None:
            current = self._current()
            if current is not None:
                if current != self._expected:
                    raise RuntimeError("image does not match frozen expected identity")
                self._store("active", True)
                return current
            state = self._store("pull_intent", False)
        phase = str(state.get("phase") or "")
        if phase == "active":
            if self._current() != self._expected:
                raise RuntimeError("leased image identity changed")
            return self._expected
        if phase != "pull_intent":
            raise RuntimeError(f"image lease is not acquirable: {phase}")
        current = self._current()
        pulled_now = False
        if current is None:
            self._runtime.pull(self._tag, self._platform)
            pulled_now = True
            current = self._current()
        if current is None:
            raise RuntimeError("pull completed without an inspectable exact tag")
        if current != self._expected:
            self._store("retained_identity_mismatch", False)
            raise RuntimeError("pulled image does not match frozen expected identity")
        self._store("active", not pulled_now)
        return current

    def release(self) -> bool:
        state = self._load()
        if state is None:
            raise RuntimeError("image lease was never acquired")
        phase = str(state.get("phase") or "")
        if phase == "released" or phase.startswith("retained_"):
            return False
        if bool(state.get("preexisting")):
            self._store("released", True)
            return False
        current = self._current()
        if phase == "pull_intent":
            phase = "retained_unsealed" if current is not None else "released"
            self._store(phase, False)
            return False
        sealed = self._expected
        if current is None:
            self._store("released", False)
            return False
        if current != sealed:
            self._store("retained_identity_mismatch", False)
            return False
        self._store("release_intent", False)
        self._runtime.remove_exact_tag(self._tag)
        if self._current() is not None:
            self._store("retained_remove_failed", False)
            return False
        self._store("released", False)
        return True

    def _load(self) -> dict[str, Any] | None:
        state = self._journal.read(self._path)
        if state is not None and state.get("identity") != self._expected:
            raise ValueError("image lease identity changed; use a new state path")
        return state

    def _store(self, phase: str, preexisting: bool) -> dict[str, Any]:
        state: dict[str, Any] = {
            "phase": phase,
            "preexisting": preexisting,
            "identity": dict(self._expected),
        }
        self._journal.write(self._path, state)
        return state

    def _current(self) -> ExactImageIdentity | None:
        current = self._runtime.inspect(self._tag)
        if current is None:
            return None
        return normalize_exact_image_identity(self._tag, self._platform, current)


def normalize_exact_image_identity(
    tag: str,
    platform: str,
    value: Mapping[str, Any],
) -> ExactImageIdentity:
    """规范并验证可跨 image seal 与 lease 复用的精确 OCI 身份。"""

    validate_exact_image_coordinates(tag, platform)
    identity: ExactImageIdentity = {
        "tag": str(value.get("tag") or ""),
        "repo_digest": str(value.get("repo_digest") or ""),
        "image_id": str(value.get("image_id") or ""),
        "platform": str(value.get("platform") or ""),
    }
    repository = tag.rsplit(":", 1)[0]
    if (
        identity["tag"] != tag
        or identity["platform"] != platform
        or not identity["image_id"].startswith("sha256:")
        or identity["image_id"] == "sha256:"
        or not _SHA256.fullmatch(identity["image_id"].removeprefix("sha256:"))
        or not identity["repo_digest"].startswith(f"{repository}@sha256:")
        or identity["repo_digest"] == f"{repository}@sha256:"
        or not _SHA256.fullmatch(
            identity["repo_digest"].removeprefix(f"{repository}@sha256:")
        )
    ):
        raise ValueError("image identity does not exactly match tag and platform")
    return identity


def _safe_token(value: str, label: str) -> None:
    if value in {".", ".."} or not _SAFE_TOKEN.fullmatch(value):
        raise ValueError(f"{label} must be a safe single path component")


def validate_exact_image_coordinates(tag: str, platform: str) -> None:
    """拒绝含歧义或可被 CLI 当作 option/path 的 image 坐标。"""

    tag_parts = tag.split("/")
    platform_parts = platform.split("/")
    if (
        not tag
        or tag.startswith("-")
        or "@" in tag
        or any(part in {"", ".", ".."} for part in tag_parts)
        or ":" not in tag_parts[-1]
        or any(character.isspace() for character in tag)
    ):
        raise ValueError("image must be an exact, traversal-free tag")
    if not 2 <= len(platform_parts) <= 3 or any(
        not _SAFE_TOKEN.fullmatch(part) for part in platform_parts
    ):
        raise ValueError("platform must be an exact os/architecture[/variant]")
