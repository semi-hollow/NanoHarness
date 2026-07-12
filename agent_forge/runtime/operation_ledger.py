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
    pre_fingerprint: dict[str, Any] | None = None
    post_fingerprint: dict[str, Any] | None = None
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperationLedgerStore:
    """Filesystem-backed idempotency ledger for side-effectful agent operations."""

    def __init__(self, root: str | Path = ".agent_forge/operation_ledger") -> None:
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

    @staticmethod
    def operation_fingerprint(
        tool_name: str,
        arguments: dict[str, Any],
        workspace: str,
        action: str = "",
    ) -> dict[str, Any]:
        """Capture the target state used to approve or replay an operation.

        This is intentionally small and deterministic. It does not try to model
        every side effect; it only gives the runtime enough evidence to avoid
        reusing an old approval or idempotency decision after the target changed.
        """

        args = arguments or {}
        root = Path(workspace).resolve()
        path_value = _target_path_value(args)
        if path_value:
            raw_path = Path(str(path_value))
            resolved = (raw_path if raw_path.is_absolute() else root / raw_path).resolve()
            fingerprint: dict[str, Any] = {
                "kind": "path",
                "tool_name": tool_name,
                "action": action,
                "path": str(path_value),
                "resolved_path": str(resolved),
                "inside_workspace": _is_relative_to(resolved, root),
            }
            if fingerprint["inside_workspace"] and resolved.exists() and resolved.is_file():
                content = resolved.read_bytes()
                fingerprint.update(
                    {
                        "exists": True,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                )
            else:
                fingerprint.update({"exists": False, "sha256": "", "size": 0})
            return fingerprint

        if action == "run_command" or tool_name == "run_command":
            return {
                "kind": "command",
                "tool_name": tool_name,
                "action": action,
                "command": str(args.get("command", "")),
            }

        raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        return {
            "kind": "operation",
            "tool_name": tool_name,
            "action": action,
            "arguments_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        }

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
        pre_fingerprint: dict[str, Any] | None = None,
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
            pre_fingerprint=pre_fingerprint,
        )

    def record_approved(self, operation_key: str, *, run_id: str, step: int) -> OperationRecord:
        record = self._require(operation_key)
        return self._transition(record, "approved", run_id=run_id, step=step)

    def record_executed(
        self,
        operation_key: str,
        *,
        run_id: str,
        step: int,
        observation: str,
        post_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        record = self._require(operation_key)
        record.observation = observation
        return self._transition(record, "executed", run_id=run_id, step=step, post_fingerprint=post_fingerprint)

    def record_failed(
        self,
        operation_key: str,
        *,
        run_id: str,
        step: int,
        observation: str,
        post_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        record = self._require(operation_key)
        record.observation = observation
        return self._transition(record, "failed", run_id=run_id, step=step, post_fingerprint=post_fingerprint)

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
        pre_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        existing = self.get(operation_key)
        if existing is not None:
            if existing.pre_fingerprint is None and pre_fingerprint is not None:
                existing.pre_fingerprint = pre_fingerprint
                self._write(existing)
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
            pre_fingerprint=pre_fingerprint,
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
        pre_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        existing = self.get(operation_key)
        if existing is not None:
            return self._transition(existing, status, run_id=run_id, step=step, pre_fingerprint=pre_fingerprint)
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
            pre_fingerprint=pre_fingerprint,
        )
        self._write(record)
        return record

    def _transition(
        self,
        record: OperationRecord,
        status: str,
        *,
        run_id: str,
        step: int,
        pre_fingerprint: dict[str, Any] | None = None,
        post_fingerprint: dict[str, Any] | None = None,
    ) -> OperationRecord:
        record.status = status
        record.run_id = run_id
        record.step = step
        record.updated_at = time.time()
        if record.pre_fingerprint is None and pre_fingerprint is not None:
            record.pre_fingerprint = pre_fingerprint
        if post_fingerprint is not None:
            record.post_fingerprint = post_fingerprint
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


def _target_path_value(arguments: dict[str, Any]) -> Any:
    """Return the conventional target-path argument for write-like tools."""

    for key in ("path", "file", "target_path", "output_path"):
        value = arguments.get(key)
        if value:
            return value
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    """Path.is_relative_to without locking the project to one Python minor."""

    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
