"""TaskSessionCatalogPort 的本地 JSON Adapter。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from agent_forge.operator_console.domain import TaskSession
from agent_forge.operator_console.ports import TaskSessionCatalogPort


class JsonTaskSessionCatalog(TaskSessionCatalogPort):
    """每个 Session 独立一份 JSON，避免全局大文件互相覆盖。

    这里显式继承 ``TaskSessionCatalogPort``，功能上并非 Python 必需，但可以让读者和
    IDE 一眼看到 Port/Adapter 关系。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._lock = RLock()

    def save(self, session: TaskSession) -> None:
        """使用临时文件替换，防止进程中断留下半份 JSON。"""

        path = self._path_for(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        payload = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
        with self._lock:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)

    def get(self, session_id: str) -> TaskSession | None:
        path = self._path_for(session_id)
        if not path.is_file():
            return None
        with self._lock:
            return self._read(path)

    def list_all(self) -> list[TaskSession]:
        if not self.root.is_dir():
            return []
        sessions: list[TaskSession] = []
        with self._lock:
            for path in sorted(self.root.glob("*.json")):
                try:
                    sessions.append(self._read(path))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
        return sessions

    def _path_for(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
            raise ValueError(f"invalid task session id: {session_id!r}")
        return self.root / f"{session_id}.json"

    @staticmethod
    def _read(path: Path) -> TaskSession:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"task session root must be an object: {path}")
        return TaskSession.from_dict(payload)
