from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"responded", "cancelled"}
REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")


@dataclass
class HumanInputRequest:
    """One durable question waiting for an operator response."""

    request_id: str
    thread_id: str
    status: str
    kind: str
    question: str
    choices: list[str]
    answer: str
    workspace: str
    run_id: str
    step: int
    agent_name: str
    reason: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    response_note: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HumanInputStore:
    """Filesystem-backed queue for non-blocking human questions."""

    def __init__(self, root: str | Path = ".agent_forge/human_input"):
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

    def request(
        self,
        *,
        thread_id: str,
        kind: str,
        question: str,
        choices: list[str] | None,
        workspace: str,
        run_id: str,
        step: int,
        agent_name: str,
        reason: str,
    ) -> HumanInputRequest:
        question = str(question or "").strip()
        if not question:
            raise ValueError("human input question must not be empty")
        normalized_choices = list(
            dict.fromkeys(str(item).strip() for item in choices or [] if str(item).strip())
        )
        request_id = self.request_id(thread_id, kind, question, normalized_choices)
        existing = self.get(request_id)
        if existing is not None:
            return existing
        request = HumanInputRequest(
            request_id=request_id,
            thread_id=thread_id,
            status="pending",
            kind=kind,
            question=question,
            choices=normalized_choices,
            answer="",
            workspace=str(Path(workspace).resolve()),
            run_id=run_id,
            step=step,
            agent_name=agent_name,
            reason=reason,
        )
        self._write(request)
        return request

    def list_all(self) -> list[HumanInputRequest]:
        requests: list[HumanInputRequest] = []
        for path in self.root.glob("*.json"):
            try:
                requests.append(HumanInputRequest(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(requests, key=lambda item: item.updated_at, reverse=True)

    def list_pending(self) -> list[HumanInputRequest]:
        return [request for request in self.list_all() if request.status == "pending"]

    def respond(self, request_id: str, answer: str, note: str = "") -> HumanInputRequest:
        answer = str(answer or "").strip()
        if not answer:
            raise ValueError("human input answer must not be empty")
        request = self._pending(request_id)
        if request.choices and answer not in request.choices:
            raise ValueError(f"answer must be one of: {', '.join(request.choices)}")
        request.status = "responded"
        request.answer = answer
        request.response_note = note
        request.updated_at = time.time()
        self._write(request)
        return request

    def cancel(self, request_id: str, note: str = "") -> HumanInputRequest:
        request = self._pending(request_id)
        request.status = "cancelled"
        request.response_note = note
        request.updated_at = time.time()
        self._write(request)
        return request

    def _pending(self, request_id: str) -> HumanInputRequest:
        request = self.get(request_id)
        if request is None:
            raise FileNotFoundError(f"human input request not found: {request_id}")
        if request.status in TERMINAL_STATUSES:
            raise ValueError(f"human input request is terminal: {request.status}")
        if request.status != "pending":
            raise ValueError(f"human input request cannot be updated from status: {request.status}")
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
