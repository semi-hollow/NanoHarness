from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from agent_forge.runtime.domain.human_input import (
    HumanInputRequest,
    HumanInputRequestDraft,
)

REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")


class JsonHumanInputRepository:
    def __init__(self, root: str | Path = ".agent_forge/human_input") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def request_id(
        thread_id: str,
        kind: str,
        question: str,
        choices: list[str] | None = None,
    ) -> str:
        payload = json.dumps(
            {
                "thread_id": thread_id,
                "kind": kind,
                "question": question.strip(),
                "choices": choices or [],
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
        path = self.path_for(request_id)
        if not path.exists():
            return None
        return HumanInputRequest(**json.loads(path.read_text(encoding="utf-8")))

    # 运行时端口：以确定性 request id 创建或复用待回答问题。
    def request(self, draft: HumanInputRequestDraft) -> HumanInputRequest:
        """创建或复用使当前运行可恢复的持久化问题。

        ``RunLifecycle.request_human_input`` 是当前 runtime owner。稳定 request id
        保证重试幂等；lifecycle 再将返回对象写入 checkpoint 和 trace。
        """

        question = str(draft.question or "").strip()
        if not question:
            raise ValueError("human input question must not be empty")
        normalized_choices = list(
            dict.fromkeys(
                str(item).strip() for item in draft.choices if str(item).strip()
            )
        )
        request_id = self.request_id(
            draft.thread_id,
            draft.kind,
            question,
            normalized_choices,
        )
        existing = self.get(request_id)
        if existing is not None:
            return existing
        request = HumanInputRequest(
            request_id=request_id,
            thread_id=draft.thread_id,
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

    # 运行时端口：只允许 pending 问题写入一次有效回答。
    def respond(
        self, request_id: str, answer: str, note: str = ""
    ) -> HumanInputRequest:
        answer = str(answer or "").strip()
        if not answer:
            raise ValueError("human input answer must not be empty")
        request = self._pending(request_id)
        request.record_answer(answer, note)
        self._write(request)
        return request

    def cancel(self, request_id: str, note: str = "") -> HumanInputRequest:
        request = self._pending(request_id)
        request.cancel(note)
        self._write(request)
        return request

    def _pending(self, request_id: str) -> HumanInputRequest:
        request = self.get(request_id)
        if request is None:
            raise FileNotFoundError(f"human input request not found: {request_id}")
        request.ensure_pending()
        return request

    def _write(self, request: HumanInputRequest) -> None:
        path = self.path_for(request.request_id)
        request.path = str(path)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(request.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
