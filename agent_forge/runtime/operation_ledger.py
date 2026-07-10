from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OperationRecord:
    """Durable state for one side-effectful tool operation."""

    operation_key: str
    status: str
    tool_name: str
    arguments: dict[str, Any]
    action: str
    workspace: str
    run_id: str = ""
    step: int = 0
    observation: str = ""
    history: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperationLedgerStore:
    """Filesystem-backed idempotency ledger for side-effectful agent operations."""

    def __init__(self, root: str | Path = ".agent_forge/operation_ledger"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def operation_key(tool_name: str, arguments: dict[str, Any], workspace: str, action: str = "") -> str:
        payload = {
            "tool_name": tool_name,
            "arguments": arguments or {},
            "workspace": str(Path(workspace).resolve()),
            "action": action,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def path_for(self, operation_key: str) -> Path:
        return self.root / f"{operation_key}.json"

    def get(self, operation_key: str) -> OperationRecord | None:
        path = self.path_for(operation_key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return OperationRecord(**data)

    def record_pending(
        self,
        operation_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        action: str,
        workspace: str,
        *,
        run_id: str,
        step: int,
    ) -> OperationRecord:
        return self._record(
            operation_key,
            "pending",
            tool_name,
            arguments,
            action,
            workspace,
            run_id=run_id,
            step=step,
        )

    def record_approved(self, operation_key: str, *, run_id: str, step: int) -> OperationRecord:
        record = self._require(operation_key)
        return self._transition(record, "approved", run_id=run_id, step=step)

    def record_executed(self, operation_key: str, *, run_id: str, step: int, observation: str) -> OperationRecord:
        record = self._require(operation_key)
        record.observation = observation
        return self._transition(record, "executed", run_id=run_id, step=step)

    def record_failed(self, operation_key: str, *, run_id: str, step: int, observation: str) -> OperationRecord:
        record = self._require(operation_key)
        record.observation = observation
        return self._transition(record, "failed", run_id=run_id, step=step)

    def ensure_planned(
        self,
        operation_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        action: str,
        workspace: str,
        *,
        run_id: str,
        step: int,
        status: str = "planned",
    ) -> OperationRecord:
        existing = self.get(operation_key)
        if existing is not None:
            return existing
        return self._record(
            operation_key,
            status,
            tool_name,
            arguments,
            action,
            workspace,
            run_id=run_id,
            step=step,
        )

    def _record(
        self,
        operation_key: str,
        status: str,
        tool_name: str,
        arguments: dict[str, Any],
        action: str,
        workspace: str,
        *,
        run_id: str,
        step: int,
    ) -> OperationRecord:
        existing = self.get(operation_key)
        if existing is not None:
            return self._transition(existing, status, run_id=run_id, step=step)
        record = OperationRecord(
            operation_key=operation_key,
            status=status,
            tool_name=tool_name,
            arguments=arguments or {},
            action=action,
            workspace=str(Path(workspace).resolve()),
            run_id=run_id,
            step=step,
            history=[status],
        )
        self._write(record)
        return record

    def _transition(self, record: OperationRecord, status: str, *, run_id: str, step: int) -> OperationRecord:
        record.status = status
        record.run_id = run_id
        record.step = step
        record.updated_at = time.time()
        if not record.history or record.history[-1] != status:
            record.history.append(status)
        self._write(record)
        return record

    def _require(self, operation_key: str) -> OperationRecord:
        record = self.get(operation_key)
        if record is None:
            raise FileNotFoundError(f"operation record not found: {operation_key}")
        return record

    def _write(self, record: OperationRecord) -> None:
        record.path = str(self.path_for(record.operation_key))
        self.path_for(record.operation_key).write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
