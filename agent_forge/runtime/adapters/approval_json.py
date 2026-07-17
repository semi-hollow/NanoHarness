from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent_forge.runtime.domain.approval import ApprovalRequest


class JsonApprovalRepository:

    def __init__(self, root: str | Path = ".agent_forge/approvals") -> None:
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

    def get(self, operation_key: str) -> ApprovalRequest | None:
        path = self.path_for(operation_key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ApprovalRequest(**data)

    # 运行时端口：按 operation key 幂等创建待审批副作用请求。
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
        """为一次副作用创建或复用持久化授权记录。

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

    # 运行时端口：将 pending 请求转换为 approved 或 rejected 并原子落盘。
    def decide(self, operation_key: str, status: str, note: str = "") -> ApprovalRequest:

        request = self.get(operation_key)
        if request is None:
            raise FileNotFoundError(f"approval request not found: {operation_key}")
        request.decide(status, note)
        self._write(request)
        return request

    def mark_stale(self, operation_key: str, note: str = "") -> ApprovalRequest:

        request = self.get(operation_key)
        if request is None:
            raise FileNotFoundError(f"approval request not found: {operation_key}")
        request.mark_stale(note)
        self._write(request)
        return request

    def _write(self, request: ApprovalRequest) -> None:
        request.path = str(self.path_for(request.operation_key))
        self.path_for(request.operation_key).write_text(
            json.dumps(request.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
