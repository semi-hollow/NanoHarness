from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ApprovalRequest:
    """One side-effect operation waiting for, or carrying, human approval."""

    operation_key: str
    status: str
    tool_name: str
    arguments: dict[str, Any]
    action: str
    command: str
    workspace: str
    run_id: str
    step: int
    agent_name: str
    reason: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    operation_fingerprint: dict[str, Any] | None = None
    decision_note: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe request data."""

        return asdict(self)


class ApprovalStore:
    """Filesystem-backed authorization queue for side-effect operations.

    Read ``request`` for the runtime pause and ``decide`` for the operator
    transition. This store authorizes a concrete fingerprinted operation; it is
    intentionally separate from informational ``HumanInputStore`` questions.
    """

    def __init__(self, root: str | Path = ".agent_forge/approvals") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def operation_key(tool_name: str, arguments: dict[str, Any], workspace: str, action: str = "") -> str:
        """Build a stable identity for a side-effect operation."""

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

    def get(self, operation_key: str) -> ApprovalRequest | None:
        path = self.path_for(operation_key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ApprovalRequest(**data)

    # RUNTIME PORT: ToolExecutionPipeline records a side effect before pausing.
    def request(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        action: str,
        command: str,
        workspace: str,
        run_id: str,
        step: int,
        agent_name: str,
        reason: str,
        operation_fingerprint: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Create or reuse the authorization record for one side effect.

        ``ToolExecutionPipeline`` 在 permission hook 返回 ASK 后调用这里。Operation
        key 和 fingerprint 将后续决定绑定到具体 tool intent 与 target state。
        """

        key = self.operation_key(tool_name, arguments, workspace, action)
        existing = self.get(key)
        if existing is not None and existing.operation_fingerprint is None and operation_fingerprint is not None:
            existing.operation_fingerprint = operation_fingerprint
            self._write(existing)
        if existing is not None:
            return existing
        request = ApprovalRequest(
            operation_key=key,
            status="pending",
            tool_name=tool_name,
            arguments=arguments or {},
            action=action,
            command=command,
            workspace=str(Path(workspace).resolve()),
            run_id=run_id,
            step=step,
            agent_name=agent_name,
            reason=reason,
            operation_fingerprint=operation_fingerprint,
        )
        request.path = str(self.path_for(key))
        self._write(request)
        return request

    def list_pending(self) -> list[ApprovalRequest]:
        """Return pending requests newest first."""

        return [
            request
            for request in self.list_all()
            if request.status == "pending"
        ]

    def list_all(self) -> list[ApprovalRequest]:
        requests: list[ApprovalRequest] = []
        for path in self.root.glob("*.json"):
            try:
                requests.append(ApprovalRequest(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(requests, key=lambda request: request.updated_at, reverse=True)

    # RUNTIME PORT: `forge approve` records the operator's authorization decision.
    def decide(self, operation_key: str, status: str, note: str = "") -> ApprovalRequest:
        """Mark one operation approved or rejected for a later continuation."""

        if status not in {"approved", "rejected"}:
            raise ValueError("approval status must be 'approved' or 'rejected'")
        request = self.get(operation_key)
        if request is None:
            raise FileNotFoundError(f"approval request not found: {operation_key}")
        request.status = status
        request.decision_note = note
        request.updated_at = time.time()
        self._write(request)
        return request

    def mark_stale(self, operation_key: str, note: str = "") -> ApprovalRequest:
        """Mark an approval unusable because target state changed after approval."""

        request = self.get(operation_key)
        if request is None:
            raise FileNotFoundError(f"approval request not found: {operation_key}")
        request.status = "stale"
        request.decision_note = note
        request.updated_at = time.time()
        self._write(request)
        return request

    def _write(self, request: ApprovalRequest) -> None:
        request.path = str(self.path_for(request.operation_key))
        self.path_for(request.operation_key).write_text(
            json.dumps(request.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
