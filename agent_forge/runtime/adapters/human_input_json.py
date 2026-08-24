"""Human Input barrier 的 crash-safe JSON Repository。

系统角色：把 Agent 的澄清问题变成可跨 Run continuation 回答的权威事实，并保证同一
问题只接受一个合法终态。
输入：``HumanInputRequestDraft`` / 人工 answer/cancel；输出：durable request。
相邻边界：RunLifecycle 决定何时停在人工屏障；本 Adapter 负责 identity、幂等与落盘。

折叠导航：1 request identity/read；2 create/list；3 answer/cancel；4 atomic lock。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Iterator

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.runtime.domain.human_input import (
    HumanInputRequest,
    HumanInputRequestDraft,
)
from agent_forge.runtime.ports.repositories import HumanInputRepository

REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
_PROCESS_LOCK_GUARD = Lock()
_PROCESS_LOCKS: dict[Path, Lock] = {}


class JsonHumanInputRepository(HumanInputRepository):
    def __init__(
        self,
        root: str | Path = ".agent_forge/internal/state/human_input",
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # region 1. Request identity 与读取
    @staticmethod
    def request_id(
        thread_id: str,
        turn_id: str,
        kind: str,
        question: str,
        choices: list[str] | None = None,
        invocation_id: str = "",
    ) -> str:
        payload = json.dumps(
            {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "kind": kind,
                "question": question.strip(),
                "choices": choices or [],
                "invocation_id": invocation_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def path_for(self, request_id: str) -> Path:
        if not REQUEST_ID_PATTERN.fullmatch(str(request_id or "")):
            raise ValueError(f"invalid human input request id: {request_id!r}")
        return self.root / f"{request_id}.json"

    def get(self, request_id: str) -> HumanInputRequest | None:
        with self._request_lock(request_id):
            return self._get_unlocked(request_id)

    def _get_unlocked(self, request_id: str) -> HumanInputRequest | None:
        path = self.path_for(request_id)
        if not path.exists():
            return None
        return HumanInputRequest(**json.loads(path.read_text(encoding="utf-8")))
    # endregion 1. Request identity 与读取结束

    # region 2. 幂等创建与导航查询
    # 运行时端口：以确定性 request id 创建或复用待回答问题。
    def request(self, draft: HumanInputRequestDraft) -> HumanInputRequest:
        """创建或复用使当前运行可恢复的持久化问题。

        ``RunLifecycle.request_human_input`` 是当前 runtime owner。稳定 request id
        保证重试幂等；lifecycle 再将返回对象写入 checkpoint 和 trace。
        """

        question = str(draft.question or "").strip()
        if not question:
            raise ValueError("human input question must not be empty")
        # 选择项先去空、去重，确保同一语义问题生成相同 request id。
        normalized_choices = list(
            dict.fromkeys(
                str(item).strip() for item in draft.choices if str(item).strip()
            )
        )
        request_id = self.request_id(
            draft.thread_id,
            draft.turn_id,
            draft.kind,
            question,
            normalized_choices,
            draft.invocation_id,
        )
        with self._request_lock(request_id):
            existing = self._get_unlocked(request_id)
            if existing is not None:
                return existing
            request = HumanInputRequest(
                request_id=request_id,
                thread_id=draft.thread_id,
                turn_id=draft.turn_id,
                status="pending",
                kind=draft.kind,
                question=question,
                choices=normalized_choices,
                answer="",
                workspace=str(Path(draft.workspace).resolve()),
                run_id=draft.run_id,
                step=draft.step,
                agent_name=draft.agent_name,
                reason=draft.reason,
                invocation_id=draft.invocation_id,
            )
            self._write(request)
            return request

    def list_all(self) -> list[HumanInputRequest]:
        requests: list[HumanInputRequest] = []
        for path in self.root.glob("*.json"):
            try:
                requests.append(
                    HumanInputRequest(**json.loads(path.read_text(encoding="utf-8")))
                )
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(requests, key=lambda item: item.updated_at, reverse=True)

    def list_pending(self) -> list[HumanInputRequest]:
        return [request for request in self.list_all() if request.status == "pending"]
    # endregion 2. 幂等创建与导航查询结束

    # region 3. 权威回答或取消：只允许 pending -> terminal
    # 运行时端口：只允许 pending 问题写入一次有效回答。
    def respond(
        self, request_id: str, answer: str, note: str = ""
    ) -> HumanInputRequest:
        answer = str(answer or "").strip()
        if not answer:
            raise ValueError("human input answer must not be empty")
        with self._request_lock(request_id):
            request = self._require_unlocked(request_id)
            request.record_answer(answer, note)
            self._write(request)
            return request

    def cancel(self, request_id: str, note: str = "") -> HumanInputRequest:
        with self._request_lock(request_id):
            request = self._require_unlocked(request_id)
            request.cancel(note)
            self._write(request)
            return request

    def _require_unlocked(self, request_id: str) -> HumanInputRequest:
        request = self._get_unlocked(request_id)
        if request is None:
            raise FileNotFoundError(f"human input request not found: {request_id}")
        return request
    # endregion 3. 权威回答或取消结束

    # region 4. 原子写与同 request 跨线程/进程互斥
    def _write(self, request: HumanInputRequest) -> None:
        path = self.path_for(request.request_id)
        request.path = str(path)
        atomic_write_json(path, request.to_dict())

    @contextmanager
    def _request_lock(self, request_id: str) -> Iterator[None]:
        """串行化同一人工问题的创建与终态决策，禁止权威事实被覆盖。"""

        # 先做 ID 校验，确保 lock file 永远留在仓储根目录内。
        self.path_for(request_id)
        lock_path = (self.root / f".{request_id}.lock").resolve()
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
