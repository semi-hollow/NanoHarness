"""在任何模型调用前，顺序封存 evaluator image 的精确身份。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_forge.bench.application.campaign_lifecycle import (
    ExactImageIdentity,
    ExactImageRuntimePort,
    normalize_exact_image_identity,
    validate_exact_image_coordinates,
)
from agent_forge.bench.ports.campaign import CampaignJournalPort


@dataclass(frozen=True)
class ImageSealRequest:
    tag: str
    platform: str


class SequentialImageSealer:
    """只记录镜像事实；不接触 case outcome、prediction 或 official result。"""

    def __init__(
        self,
        journal: CampaignJournalPort,
        state_path: str | Path,
        runtime: ExactImageRuntimePort,
        *,
        minimum_free_bytes: int,
        free_space_probe: Callable[[], int],
    ) -> None:
        if not isinstance(minimum_free_bytes, int) or minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must be a non-negative integer")
        self._journal = journal
        self._path = journal.resolve(state_path)
        self._runtime = runtime
        self._minimum_free_bytes = minimum_free_bytes
        self._free_space_probe = free_space_probe

    def seal(
        self,
        requests: tuple[ImageSealRequest, ...],
    ) -> tuple[ExactImageIdentity, ...]:
        plan = self._plan(requests)
        state = self._load_or_create(plan)
        if state["status"] == "failed":
            raise RuntimeError("image seal is failed; use a new state path")
        if state["status"] == "complete":
            return self._identities(state)

        entries = state["entries"]
        for index, request in enumerate(requests):
            entry = entries[index]
            if entry["phase"] != "complete":
                self._seal_one(state, entry, request)
        state["status"] = "complete"
        self._write(state)
        return self._identities(state)

    def _seal_one(
        self, state: dict[str, Any], entry: dict[str, Any], request: ImageSealRequest
    ) -> None:
        phase = str(entry["phase"])
        if phase == "release_intent":
            self._recover_release(state, entry, request)
            return
        if phase == "sealed_owned":
            self._cleanup_owned(state, entry, request)
            return

        current = self._inspect(request)
        if phase == "pull_intent" and current is not None:
            self._complete(
                state, entry, current, False, "retained_uncertain_after_pull"
            )
            return
        if phase in {"pending", "blocked_insufficient_space"} and current is not None:
            self._complete(state, entry, current, True, "retained_preexisting")
            return
        if phase not in {"pending", "blocked_insufficient_space", "pull_intent"}:
            raise ValueError(f"invalid image seal phase: {phase}")

        free_bytes = self._free_space_probe()
        if not isinstance(free_bytes, int) or free_bytes < 0:
            raise RuntimeError("free-space probe returned an invalid byte count")
        entry["free_bytes_before_pull"] = free_bytes
        if free_bytes < self._minimum_free_bytes:
            entry["phase"] = "blocked_insufficient_space"
            self._write(state)
            raise RuntimeError("insufficient free space for sequential image pull")
        entry["phase"] = "pull_intent"
        entry["preexisting"] = False
        self._write(state)
        self._runtime.pull(request.tag, request.platform)
        identity = self._inspect(request)
        if identity is None:
            raise RuntimeError("image pull completed without an inspectable exact tag")
        entry["phase"] = "sealed_owned"
        entry["identity"] = identity
        self._write(state)
        self._cleanup_owned(state, entry, request)

    def _cleanup_owned(
        self, state: dict[str, Any], entry: dict[str, Any], request: ImageSealRequest
    ) -> None:
        expected = self._entry_identity(entry, request)
        if self._inspect(request) != expected:
            self._fail_identity_change(state, entry)
        entry["phase"] = "release_intent"
        self._write(state)
        self._runtime.remove_exact_tag(request.tag)
        current = self._inspect(request)
        if current is not None:
            self._fail_identity_change(state, entry)
        self._complete(state, entry, expected, False, "removed_exact_tag")

    def _recover_release(
        self, state: dict[str, Any], entry: dict[str, Any], request: ImageSealRequest
    ) -> None:
        expected = self._entry_identity(entry, request)
        current = self._inspect(request)
        if current is not None and current != expected:
            self._fail_identity_change(state, entry)
        cleanup = (
            "removed_before_recovery"
            if current is None
            else "retained_release_uncertain"
        )
        self._complete(state, entry, expected, False, cleanup)

    def _inspect(self, request: ImageSealRequest) -> ExactImageIdentity | None:
        current = self._runtime.inspect(request.tag)
        if current is None:
            return None
        return normalize_exact_image_identity(request.tag, request.platform, current)

    def _complete(
        self,
        state: dict[str, Any],
        entry: dict[str, Any],
        identity: ExactImageIdentity,
        preexisting: bool,
        cleanup: str,
    ) -> None:
        entry.update(
            phase="complete",
            identity=dict(identity),
            preexisting=preexisting,
            cleanup=cleanup,
        )
        self._write(state)

    def _fail_identity_change(
        self, state: dict[str, Any], entry: dict[str, Any]
    ) -> None:
        entry["phase"] = "failed_identity_mismatch"
        entry["cleanup"] = "retained_identity_mismatch"
        state["status"] = "failed"
        self._write(state)
        raise RuntimeError("image identity changed during exact-tag cleanup")

    def _load_or_create(self, plan: list[dict[str, str]]) -> dict[str, Any]:
        state = self._journal.read(self._path)
        if state is None:
            state = {
                "schema_version": 1,
                "status": "in_progress",
                "plan": plan,
                "entries": [
                    {"index": index, **item, "phase": "pending"}
                    for index, item in enumerate(plan)
                ],
            }
            self._write(state)
            return state
        entries = state.get("entries")
        if (
            state.get("schema_version") != 1
            or state.get("plan") != plan
            or state.get("status") not in {"in_progress", "complete", "failed"}
            or not isinstance(entries, list)
            or len(entries) != len(plan)
        ):
            raise ValueError("image seal plan or state changed; use a new state path")
        for index, item in enumerate(plan):
            entry = entries[index]
            if not isinstance(entry, dict) or any(
                entry.get(key) != value
                for key, value in {"index": index, **item}.items()
            ):
                raise ValueError("image seal entries do not match the frozen plan")
        return state

    def _identities(self, state: dict[str, Any]) -> tuple[ExactImageIdentity, ...]:
        identities = []
        for entry in state["entries"]:
            if entry.get("phase") != "complete":
                raise ValueError("complete image seal contains an incomplete entry")
            request = ImageSealRequest(str(entry["tag"]), str(entry["platform"]))
            identities.append(self._entry_identity(entry, request))
        return tuple(identities)

    @staticmethod
    def _entry_identity(
        entry: dict[str, Any], request: ImageSealRequest
    ) -> ExactImageIdentity:
        identity = entry.get("identity")
        if not isinstance(identity, dict):
            raise ValueError("image seal entry is missing its exact identity")
        return normalize_exact_image_identity(request.tag, request.platform, identity)

    @staticmethod
    def _plan(requests: tuple[ImageSealRequest, ...]) -> list[dict[str, str]]:
        if not requests or len({request.tag for request in requests}) != len(requests):
            raise ValueError("image seal requests must be non-empty with unique tags")
        plan = []
        for request in requests:
            validate_exact_image_coordinates(request.tag, request.platform)
            plan.append({"tag": request.tag, "platform": request.platform})
        return plan

    def _write(self, state: dict[str, Any]) -> None:
        self._journal.write(self._path, state)
