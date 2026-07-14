from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from agent_forge.runtime.domain.human_input import HumanInputRequest

REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")


class JsonHumanInputRepository:
    """Filesystem-backed queue for non-blocking human questions.

    Runtime flow: ``request`` creates the pause record, ``respond`` records an
    operator answer, and ``forge resume`` consumes it. Read those two ports;
    path, listing, and atomic-write helpers are storage details.
    """

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

    # RUNTIME PORT: RunLifecycle persists a question before stopping the run.
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
        """Create or reuse the durable question that makes a run resumable.

        ``RunLifecycle.request_human_input`` 是当前 runtime owner。稳定 request id
        保证重试幂等；lifecycle 再将返回对象写入 checkpoint 和 trace。
        """

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

    # RUNTIME PORT: `forge respond` moves a pending question to responded.
    def respond(self, request_id: str, answer: str, note: str = "") -> HumanInputRequest:
        """Persist one validated answer for later ``forge resume`` consumption."""

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


HumanInputStore = JsonHumanInputRepository
