"""Human Approval 的 crash-safe JSON Repository。

系统角色：将一次具体副作用 intent 变成可由人工决定的 durable request，并保证并发
continuation 不会覆盖同一授权事实。
输入：``ApprovalRequestDraft`` / 人工 decision；输出：稳定 ``ApprovalRequest``。
相邻边界：Authorization Gate 决定是否 ASK；本 Adapter 只持久化 pending/decision/stale。

折叠导航：1 identity/read；2 request/list；3 decision；4 atomic write/lock。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.runtime.domain.approval import ApprovalRequest, ApprovalRequestDraft
from agent_forge.runtime.ports.repositories import ApprovalRepository


_PROCESS_LOCK_GUARD = Lock()
_PROCESS_LOCKS: dict[Path, Lock] = {}


class JsonApprovalRepository(ApprovalRepository):
    def __init__(
        self,
        root: str | Path = ".agent_forge/internal/state/approvals",
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # region 1. Identity 与读取
    @staticmethod
    def operation_key(
        tool_name: str, arguments: dict[str, Any], workspace: str, action: str = ""
    ) -> str:
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
        with self._operation_lock(operation_key):
            return self._get_unlocked(operation_key)

    def _get_unlocked(self, operation_key: str) -> ApprovalRequest | None:
        path = self.path_for(operation_key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ApprovalRequest(**data)
    # endregion 1. Identity 与读取结束

    # region 2. 幂等请求与导航查询
    # 运行时端口：按 operation key 幂等创建尚未执行的操作审批请求。
    def request(self, draft: ApprovalRequestDraft) -> ApprovalRequest:
        """为一次可能改变持久状态的操作创建或复用持久化授权记录。

        ``ToolExecutionPipeline`` 在 permission hook 返回 ASK 后调用这里。Operation
        key 和 fingerprint 将后续决定绑定到具体 tool intent 与 target state。
        """

        key = draft.operation_key or self.operation_key(
            draft.tool_name,
            draft.arguments,
            draft.workspace,
            draft.action,
        )
        if not re.fullmatch(r"[0-9a-f]{24}", key):
            raise ValueError("approval operation_key must be a 24-character hex digest")
        with self._operation_lock(key):
            existing = self._get_unlocked(key)
            # stale 只否定旧 fingerprint 的授权；同一操作意图在新目标状态上
            # 必须能重新进入 pending，仍使用同一稳定 operation key。
            if existing is not None and existing.status == "stale":
                existing = None
            if (
                existing is not None
                and existing.status == "pending"
                and existing.operation_fingerprint is None
                and draft.operation_fingerprint is not None
            ):
                existing.operation_fingerprint = draft.operation_fingerprint
                self._write(existing)
            if existing is not None:
                return existing
            request = ApprovalRequest(
                operation_key=key,
                status="pending",
                tool_name=draft.tool_name,
                arguments=draft.arguments,
                action=draft.action,
                command=draft.command,
                workspace=str(Path(draft.workspace).resolve()),
                run_id=draft.run_id,
                step=draft.step,
                agent_name=draft.agent_name,
                reason=draft.reason,
                operation_fingerprint=draft.operation_fingerprint,
            )
            request.path = str(self.path_for(key))
            self._write(request)
            return request

    def list_pending(self) -> list[ApprovalRequest]:
        return [request for request in self.list_all() if request.status == "pending"]

    def list_all(self) -> list[ApprovalRequest]:
        requests: list[ApprovalRequest] = []
        # 单个坏文件被隔离，不能阻断操作员查看其余待审批请求。
        for path in self.root.glob("*.json"):
            try:
                requests.append(
                    ApprovalRequest(**json.loads(path.read_text(encoding="utf-8")))
                )
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(requests, key=lambda request: request.updated_at, reverse=True)
    # endregion 2. 幂等请求与导航查询结束

    # region 3. 人工决定与失效：Domain 对象校验合法终态
    # 运行时端口：将 pending 请求转换为 approved 或 rejected 并原子落盘。
    def decide(
        self, operation_key: str, status: str, note: str = ""
    ) -> ApprovalRequest:
        with self._operation_lock(operation_key):
            request = self._get_unlocked(operation_key)
            if request is None:
                raise FileNotFoundError(f"approval request not found: {operation_key}")
            request.decide(status, note)
            self._write(request)
            return request

    def mark_stale(self, operation_key: str, note: str = "") -> ApprovalRequest:
        with self._operation_lock(operation_key):
            request = self._get_unlocked(operation_key)
            if request is None:
                raise FileNotFoundError(f"approval request not found: {operation_key}")
            request.mark_stale(note)
            self._write(request)
            return request
    # endregion 3. 人工决定与失效结束

    # region 4. 原子写与同 key 跨线程/进程互斥
    def _write(self, request: ApprovalRequest) -> None:
        request.path = str(self.path_for(request.operation_key))
        atomic_write_json(self.path_for(request.operation_key), request.to_dict())

    @contextmanager
    def _operation_lock(self, operation_key: str) -> Iterator[None]:
        """同一审批键的创建、决定与失效必须串行，禁止 last-write-wins。"""

        lock_path = (self.root / f".{operation_key}.lock").resolve()
        with _PROCESS_LOCK_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(lock_path, Lock())
        with process_lock:
            with lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    # endregion 4. 原子写与互斥结束
