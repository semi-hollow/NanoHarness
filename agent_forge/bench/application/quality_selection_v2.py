"""围绕共享正式实验基础设施实现精简的 v2 策略适配层。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any, Callable, Mapping, Sequence

from agent_forge.bench.application.image_sealer import (
    ImageSealRequest,
    SequentialImageSealer,
)


class QualitySelectionV2Refused(RuntimeError):
    """启动或资格门失败时拒绝进入任何正式槽位。"""


_SAFE_CANDIDATE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class QualitySelectionV2Slot:
    slot_id: str
    ordinal: int
    candidate_id: str
    case_id: str
    image_tag: str
    output_root: Path
    argv: tuple[str, ...]


def verify_quality_selection_v2_readiness(
    project_root: Path,
    readiness_path: str | Path,
) -> Mapping[str, Any]:
    """只读确认完整分母容量与 Use balance 边界。"""

    root = project_root.resolve()
    readiness = _json(_under(root, readiness_path))
    if readiness.get("status") != "go_complete_denominator_capacity":
        raise QualitySelectionV2Refused("launch readiness is not GO")
    gates = readiness.get("gates")
    if (
        not isinstance(gates, dict)
        or gates.get("weekly_covers_complete_campaign") is not True
    ):
        raise QualitySelectionV2Refused("weekly quota does not cover the campaign")
    if gates.get("use_balance_off") is not True:
        raise QualitySelectionV2Refused("Use balance must remain off")
    return readiness


def slots_from_manifest(
    manifest: Mapping[str, Any], project_root: Path
) -> tuple[QualitySelectionV2Slot, ...]:
    root = project_root.resolve()
    fixed = tuple(str(item) for item in manifest["fixed_argv"])
    slots = tuple(
        QualitySelectionV2Slot(
            slot_id=f"slot-{int(item['ordinal']):03d}",
            ordinal=int(item["ordinal"]),
            candidate_id=str(item["candidate_id"]),
            case_id=str(item["instance_ids"][0]),
            image_tag=str(item["image"]["tag"]),
            output_root=_under(root, str(item["output_root"])),
            argv=(*fixed, *(str(token) for token in item["argv_suffix"])),
        )
        for item in manifest["commands"]
    )
    if [slot.ordinal for slot in slots] != list(range(1, 21)):
        raise QualitySelectionV2Refused("formal schedule must contain ordinals 1..20")
    for first, second in zip(slots[::2], slots[1::2], strict=True):
        if (
            first.case_id != second.case_id
            or first.image_tag != second.image_tag
            or first.candidate_id == second.candidate_id
        ):
            raise QualitySelectionV2Refused("formal schedule is not Case-paired")
    return slots


class QualitySelectionV2Preflight:
    def __init__(
        self,
        *,
        project_root: Path,
        manifest: Mapping[str, Any],
        readiness_path: Path,
        source_gate: Callable[[Mapping[str, Any]], None],
        credential_gate: Callable[[Mapping[str, Any]], None],
        image_sealer: SequentialImageSealer,
        run_command: Callable[[Sequence[str]], int],
        clock: Callable[[], float],
        append_event: Callable[[Mapping[str, Any]], None],
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self._root = project_root.resolve()
        self._manifest = manifest
        self._readiness = readiness_path
        self._source_gate = source_gate
        self._credential_gate = credential_gate
        self._image_sealer = image_sealer
        self._run = run_command
        self._clock = clock
        self._append_event = append_event
        self._wait = wait
        self._event_sequence = 0
        self._provider_sequence = 0

    def qualify(self) -> tuple[Mapping[str, str], ...]:
        quiet = _positive_int(self._manifest, "initial_quiet_seconds")
        between = _positive_int(
            self._manifest, "minimum_seconds_between_provider_commands"
        )
        cooldown = _positive_int(
            self._manifest, "qualification_to_formal_cooldown_seconds"
        )
        self._source_gate(self._manifest["source_identity"])
        self._credential_gate(self._manifest["credential_preflight"])
        self._require_go_readiness()
        self._verified_wait("initial_quiet", quiet)
        slots = slots_from_manifest(self._manifest, self._root)
        unique_images = tuple(dict.fromkeys(slot.image_tag for slot in slots))
        identities = tuple(
            self._image_sealer.seal(
                tuple(ImageSealRequest(tag, "linux/amd64") for tag in unique_images)
            )
        )
        if [item.get("tag") for item in identities] != list(unique_images):
            raise QualitySelectionV2Refused(
                "image seal does not match frozen tag order"
            )
        self._run_series(self._manifest["capability_probes"], between)
        self._run_series(self._manifest["qualification_commands"], between)
        self._verified_wait("qualification_to_formal_cooldown", cooldown)
        return identities

    def _require_go_readiness(self) -> None:
        verify_quality_selection_v2_readiness(self._root, self._readiness)

    def _run_series(
        self, entries: Sequence[Mapping[str, Any]], between_wait: int
    ) -> None:
        for entry in entries:
            argv = tuple(str(item) for item in entry["argv"])
            output = _flag_value(argv, "--output")
            if _under(self._root, output).exists():
                raise QualitySelectionV2Refused("qualification output already exists")
            candidate = str(entry.get("candidate_id") or "")
            if not _SAFE_CANDIDATE.fullmatch(candidate):
                raise QualitySelectionV2Refused("unsafe qualification candidate ID")
            self._run_provider(candidate, argv, _under(self._root, output))
            self._verified_wait("between_provider_commands", between_wait)

    def _run_provider(
        self, candidate: str, argv: tuple[str, ...], output: Path
    ) -> None:
        self._provider_sequence += 1
        argv_sha256 = hashlib.sha256(
            json.dumps(argv, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        started = self._clock()
        self._emit(
            "provider_command_started",
            candidate_id=candidate,
            provider_sequence=self._provider_sequence,
            argv_sha256=argv_sha256,
            started_monotonic=started,
        )
        result = "launcher_exception"
        failure: Exception | None = None
        exit_code: int | None = None
        try:
            exit_code = self._run(argv)
            result = "exit_nonzero"
            if exit_code != 0:
                raise QualitySelectionV2Refused("qualification command failed")
            result = "evidence_invalid"
            if _json(output).get("status") != "passed":
                raise QualitySelectionV2Refused("qualification evidence is not passed")
            result = "passed"
        except Exception as exc:
            failure = exc
        ended = self._clock()
        self._emit(
            "provider_command_completed",
            candidate_id=candidate,
            provider_sequence=self._provider_sequence,
            argv_sha256=argv_sha256,
            started_monotonic=started,
            ended_monotonic=ended,
            elapsed_seconds=ended - started,
            exit_code=exit_code,
            result=result,
        )
        if failure is not None:
            if isinstance(failure, QualitySelectionV2Refused):
                raise failure
            raise QualitySelectionV2Refused(
                "qualification launcher failed"
            ) from failure

    def _verified_wait(self, phase: str, seconds: int) -> None:
        started = self._clock()
        failure: Exception | None = None
        try:
            self._wait(seconds)
        except Exception as exc:
            failure = exc
        ended = self._clock()
        elapsed = ended - started
        result = "passed" if failure is None and elapsed >= seconds else "failed"
        self._emit(
            "pacing_wait",
            phase=phase,
            required_seconds=seconds,
            started_monotonic=started,
            ended_monotonic=ended,
            elapsed_seconds=elapsed,
            result=result,
        )
        if result != "passed":
            raise QualitySelectionV2Refused(f"pacing wait was shorter than {seconds}s")

    def _emit(self, event_type: str, **fields: Any) -> None:
        self._event_sequence += 1
        self._append_event(
            {
                "schema_version": 1,
                "sequence": self._event_sequence,
                "event_type": event_type,
                **fields,
            }
        )


class QualitySelectionV2FormalLauncher:
    """按冻结顺序为共享 formal runner 增加实测 pacing 与安全事件。"""

    def __init__(
        self,
        *,
        slots: Sequence[QualitySelectionV2Slot],
        minimum_seconds: int,
        run_command: Callable[[Sequence[str]], int],
        clock: Callable[[], float],
        append_event: Callable[[Mapping[str, Any]], None],
        initial_sequence: int,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        if minimum_seconds <= 0 or initial_sequence < 0:
            raise ValueError("formal pacing inputs must be positive")
        self._slots = tuple(slots)
        self._minimum = minimum_seconds
        self._run = run_command
        self._clock = clock
        self._append = append_event
        self._wait = wait
        self._index = 0
        self._sequence = initial_sequence

    def __call__(self, argv: Sequence[str]) -> int:
        if self._index >= len(self._slots):
            raise QualitySelectionV2Refused("formal launcher exceeded frozen schedule")
        slot = self._slots[self._index]
        if tuple(argv) != slot.argv:
            raise QualitySelectionV2Refused("formal command order or argv drift")
        wait_started = self._clock()
        failure: Exception | None = None
        try:
            self._wait(self._minimum)
        except Exception as exc:
            failure = exc
        wait_ended = self._clock()
        wait_elapsed = wait_ended - wait_started
        wait_result = (
            "passed" if failure is None and wait_elapsed >= self._minimum else "failed"
        )
        self._emit(
            "formal_pacing_wait",
            slot_id=slot.slot_id,
            required_seconds=self._minimum,
            started_monotonic=wait_started,
            ended_monotonic=wait_ended,
            elapsed_seconds=wait_elapsed,
            result=wait_result,
        )
        if wait_result != "passed":
            raise QualitySelectionV2Refused("formal pacing wait was too short")
        argv_sha256 = _formal_argv_sha256(argv)
        started = self._clock()
        self._emit(
            "formal_provider_command_started",
            slot_id=slot.slot_id,
            candidate_id=slot.candidate_id,
            argv_sha256=argv_sha256,
            started_monotonic=started,
        )
        self._index += 1
        result = "launcher_exception"
        return_code: int | None = None
        caught: Exception | None = None
        try:
            return_code = self._run(argv)
            result = "passed" if return_code == 0 else "exit_nonzero"
        except Exception as exc:
            caught = exc
        ended = self._clock()
        self._emit(
            "formal_provider_command_completed",
            slot_id=slot.slot_id,
            candidate_id=slot.candidate_id,
            argv_sha256=argv_sha256,
            started_monotonic=started,
            ended_monotonic=ended,
            elapsed_seconds=ended - started,
            exit_code=return_code,
            result=result,
        )
        if caught is not None:
            raise caught
        assert return_code is not None
        return return_code

    def _emit(self, event_type: str, **fields: Any) -> None:
        self._sequence += 1
        self._append(
            {
                "schema_version": 1,
                "sequence": self._sequence,
                "event_type": event_type,
                **fields,
            }
        )


def audit_quality_selection_v2_completed_pacing(
    ledger_path: Path,
    slots: Sequence[QualitySelectionV2Slot],
    *,
    prefix_sha256: str,
    prefix_bytes: int,
    prefix_last_sequence: int = 20,
    completed_last_sequence: int = 80,
    minimum_seconds: int = 300,
) -> str:
    """只读重验完整 v2 pacing ledger，并返回全文件 SHA-256。"""

    frozen_slots = tuple(slots)
    if (
        prefix_last_sequence != 20
        or completed_last_sequence != 80
        or minimum_seconds != 300
        or len(frozen_slots) != 20
        or completed_last_sequence - prefix_last_sequence != 3 * len(frozen_slots)
        or [slot.ordinal for slot in frozen_slots] != list(range(1, 21))
        or [slot.slot_id for slot in frozen_slots]
        != [f"slot-{index:03d}" for index in range(1, 21)]
    ):
        raise QualitySelectionV2Refused("completed pacing denominator drift")
    if not re.fullmatch(r"[0-9a-f]{64}", prefix_sha256):
        raise QualitySelectionV2Refused("invalid pacing prefix SHA-256")
    if (
        not isinstance(prefix_bytes, int)
        or isinstance(prefix_bytes, bool)
        or prefix_bytes <= 0
    ):
        raise QualitySelectionV2Refused("invalid pacing prefix byte length")

    payload = _read_regular_ledger(ledger_path)
    events, line_ends = _strict_jsonl(payload)
    if len(events) != completed_last_sequence:
        raise QualitySelectionV2Refused("completed pacing event count drift")
    if (
        prefix_bytes not in line_ends
        or line_ends.index(prefix_bytes) + 1 != prefix_last_sequence
    ):
        raise QualitySelectionV2Refused("pacing prefix byte boundary drift")
    if hashlib.sha256(payload[:prefix_bytes]).hexdigest() != prefix_sha256:
        raise QualitySelectionV2Refused("pacing prefix hash drift")
    if any(
        type(event.get("schema_version")) is not int
        or event.get("schema_version") != 1
        or type(event.get("sequence")) is not int
        or event.get("sequence") != sequence
        for sequence, event in enumerate(events, start=1)
    ):
        raise QualitySelectionV2Refused("global pacing event sequence drift")

    cooldown_event = events[prefix_last_sequence - 1]
    if (
        cooldown_event.get("event_type") != "pacing_wait"
        or cooldown_event.get("phase") != "qualification_to_formal_cooldown"
    ):
        raise QualitySelectionV2Refused("formal pacing prefix boundary drift")
    previous_completed: float | None = _finite_time(
        cooldown_event.get("ended_monotonic")
    )
    for index, slot in enumerate(frozen_slots):
        offset = prefix_last_sequence + index * 3
        previous_completed = _audit_formal_pacing_triplet(
            events[offset : offset + 3],
            slot,
            first_sequence=offset + 1,
            previous_completed=previous_completed,
            minimum_seconds=minimum_seconds,
        )
    return hashlib.sha256(payload).hexdigest()


def _audit_formal_pacing_triplet(
    events: Sequence[Mapping[str, Any]],
    slot: QualitySelectionV2Slot,
    *,
    first_sequence: int,
    previous_completed: float | None,
    minimum_seconds: int,
) -> float:
    if len(events) != 3:
        raise QualitySelectionV2Refused("formal pacing triplet is incomplete")
    wait_event, started_event, completed_event = events
    _exact_event(
        wait_event,
        {
            "schema_version",
            "sequence",
            "event_type",
            "slot_id",
            "required_seconds",
            "started_monotonic",
            "ended_monotonic",
            "elapsed_seconds",
            "result",
        },
        first_sequence,
        "formal_pacing_wait",
    )
    _exact_event(
        started_event,
        {
            "schema_version",
            "sequence",
            "event_type",
            "slot_id",
            "candidate_id",
            "argv_sha256",
            "started_monotonic",
        },
        first_sequence + 1,
        "formal_provider_command_started",
    )
    _exact_event(
        completed_event,
        {
            "schema_version",
            "sequence",
            "event_type",
            "slot_id",
            "candidate_id",
            "argv_sha256",
            "started_monotonic",
            "ended_monotonic",
            "elapsed_seconds",
            "exit_code",
            "result",
        },
        first_sequence + 2,
        "formal_provider_command_completed",
    )
    if any(event.get("slot_id") != slot.slot_id for event in events):
        raise QualitySelectionV2Refused("formal pacing slot drift")
    expected_argv = _formal_argv_sha256(slot.argv)
    if any(
        event.get("candidate_id") != slot.candidate_id
        or event.get("argv_sha256") != expected_argv
        for event in (started_event, completed_event)
    ):
        raise QualitySelectionV2Refused("formal pacing candidate or argv drift")

    wait_started = _finite_time(wait_event.get("started_monotonic"))
    wait_ended = _finite_time(wait_event.get("ended_monotonic"))
    wait_elapsed = _finite_time(wait_event.get("elapsed_seconds"))
    required = wait_event.get("required_seconds")
    if (
        type(required) is not int
        or required != minimum_seconds
        or wait_event.get("result") != "passed"
        or wait_ended < wait_started
        or wait_elapsed != wait_ended - wait_started
        or wait_elapsed < required
        or (previous_completed is not None and wait_started < previous_completed)
    ):
        raise QualitySelectionV2Refused("formal pacing wait evidence drift")

    started = _finite_time(started_event.get("started_monotonic"))
    completed_started = _finite_time(completed_event.get("started_monotonic"))
    completed_ended = _finite_time(completed_event.get("ended_monotonic"))
    completed_elapsed = _finite_time(completed_event.get("elapsed_seconds"))
    if (
        started != completed_started
        or started < wait_ended
        or completed_ended < started
        or completed_elapsed != completed_ended - started
        or type(completed_event.get("exit_code")) is not int
        or completed_event.get("exit_code") != 0
        or completed_event.get("result") != "passed"
    ):
        raise QualitySelectionV2Refused("formal provider completion evidence drift")
    return completed_ended


def _exact_event(
    event: Mapping[str, Any],
    keys: set[str],
    sequence: int,
    event_type: str,
) -> None:
    if (
        set(event) != keys
        or event.get("schema_version") != 1
        or event.get("sequence") != sequence
        or event.get("event_type") != event_type
    ):
        raise QualitySelectionV2Refused("formal pacing event schema drift")


def _finite_time(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualitySelectionV2Refused("formal pacing time is invalid")
    number = float(value)
    if not math.isfinite(number):
        raise QualitySelectionV2Refused("formal pacing time is invalid")
    return number


def _read_regular_ledger(path: Path) -> bytes:
    raw = Path(path)
    if raw.is_symlink():
        raise QualitySelectionV2Refused("pacing ledger cannot be a symlink")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(raw, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise QualitySelectionV2Refused("pacing ledger is not a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    except QualitySelectionV2Refused:
        raise
    except OSError as exc:
        raise QualitySelectionV2Refused("cannot read pacing ledger") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _strict_jsonl(payload: bytes) -> tuple[list[dict[str, Any]], list[int]]:
    if not payload or not payload.endswith(b"\n") or b"\r" in payload:
        raise QualitySelectionV2Refused("pacing ledger is not complete JSONL")
    events: list[dict[str, Any]] = []
    line_ends: list[int] = []
    offset = 0
    for raw_line in payload.splitlines(keepends=True):
        offset += len(raw_line)
        line_ends.append(offset)
        try:
            value = json.loads(
                raw_line[:-1].decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise QualitySelectionV2Refused(
                "pacing ledger is not strict JSONL"
            ) from exc
        if not isinstance(value, dict):
            raise QualitySelectionV2Refused("pacing ledger event is not an object")
        events.append(value)
    return events, line_ends


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _formal_argv_sha256(argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(tuple(argv), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _flag_value(argv: Sequence[str], flag: str) -> str:
    positions = [index for index, item in enumerate(argv) if item == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise QualitySelectionV2Refused(f"invalid command flag: {flag}")
    return argv[positions[0] + 1]


def _positive_int(manifest: Mapping[str, Any], key: str) -> int:
    value = manifest.get("pacing", {}).get(key)
    if not isinstance(value, int) or value <= 0:
        raise QualitySelectionV2Refused(f"invalid pacing field: {key}")
    return value


def _under(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root):
        raise QualitySelectionV2Refused(f"path escapes project root: {value}")
    return resolved


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualitySelectionV2Refused(f"cannot read sealed JSON: {path}") from exc
    if not isinstance(value, dict):
        raise QualitySelectionV2Refused(f"sealed JSON is not an object: {path}")
    return value
