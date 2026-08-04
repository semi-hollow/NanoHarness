"""Task Session 的创建、导航和历史 Run 收口。"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from agent_forge.harness_contracts import RunResult
from agent_forge.observability.api import read_run_manifest
from agent_forge.operator_console.domain import TaskSession, TaskSessionRun
from agent_forge.operator_console.ports import TaskSessionCatalogPort
from agent_forge.runtime.api import latest_checkpoint_path, load_task_checkpoint


class TaskSessionLibrary:
    """人类会话目录的唯一 Application Service。

    它只保存 Run 的索引与关系；每次运行的 Trace、Diff、Usage 和 Checkpoint 仍以
    artifact 目录为权威来源。随机 Run ID 因此无需暴露给日常操作，也无需被替换。
    """

    def __init__(self, catalog: TaskSessionCatalogPort) -> None:
        self.catalog = catalog

    # 主要入口：创建一个有稳定身份和人类标题的新任务会话。
    def create(
        self,
        *,
        task: str,
        workspace: str | Path,
        title: str = "",
    ) -> TaskSession:
        now = time.time()
        session_id = f"session-{uuid.uuid4().hex[:12]}"
        session = TaskSession(
            session_id=session_id,
            human_thread_id=session_id,
            title=self._normalize_title(title or task),
            initial_task=task.strip(),
            workspace=str(Path(workspace).expanduser().resolve()),
            created_at=now,
            updated_at=now,
        )
        self.catalog.save(session)
        return session

    # 主要入口：按置顶和最近更新时间返回可操作会话。
    def list_active(self) -> list[TaskSession]:
        return sorted(
            (session for session in self.catalog.list_all() if not session.archived),
            key=lambda session: (not session.pinned, -session.updated_at),
        )

    def require(self, session_id: str) -> TaskSession:
        session = self.catalog.get(session_id)
        if session is None:
            raise KeyError(f"task session not found: {session_id}")
        return session

    def rename(self, session_id: str, title: str) -> TaskSession:
        session = self.require(session_id)
        renamed = replace(
            session,
            title=self._normalize_title(title),
            updated_at=time.time(),
        )
        self.catalog.save(renamed)
        return renamed

    def set_archived(self, session_id: str, archived: bool = True) -> TaskSession:
        session = self.require(session_id)
        updated = replace(session, archived=archived, updated_at=time.time())
        self.catalog.save(updated)
        return updated

    def toggle_pinned(self, session_id: str) -> TaskSession:
        session = self.require(session_id)
        updated = replace(
            session,
            pinned=not session.pinned,
            updated_at=time.time(),
        )
        self.catalog.save(updated)
        return updated

    # 主要入口：一次 Harness 结束后，把不可变 Run 索引挂到当前 Session。
    def record_result(
        self,
        session_id: str,
        result: RunResult,
        *,
        relationship: str,
        parent_run_id: str = "",
    ) -> TaskSession:
        checkpoint_path = result.artifact_dir / "task_state" / f"{result.run_id}.json"
        if not checkpoint_path.is_file():
            checkpoint_path = Path(latest_checkpoint_path(str(result.artifact_dir)))
        run = TaskSessionRun(
            run_id=result.run_id,
            task=result.checkpoint.task,
            artifact_dir=str(result.artifact_dir.resolve()),
            workspace=self._requested_workspace(result.checkpoint.to_dict()),
            checkpoint_path=str(checkpoint_path.resolve()),
            status=result.status.value,
            stop_reason=result.stop_reason,
            current_step=result.checkpoint.current_step,
            relationship=relationship,
            parent_run_id=parent_run_id,
            created_at=result.checkpoint.created_at,
            updated_at=result.checkpoint.updated_at,
        )
        return self._save_run(session_id, run)

    # 主要入口：首次启用会话库时，把已有 artifact 按 human_thread_id 归组。
    def import_existing_runs(self, output_root: str | Path) -> int:
        """幂等导入旧 Run；损坏或未收口目录不会阻断 Console 启动。"""

        root = Path(output_root).expanduser().resolve()
        if not root.is_dir():
            return 0
        imported = 0
        sessions_by_thread_id = {
            session.human_thread_id: session for session in self.catalog.list_all()
        }
        for run_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            try:
                run = self._read_existing_run(run_dir)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if run is None:
                continue
            human_thread_id, run_record = run
            session = sessions_by_thread_id.get(human_thread_id)
            if session is None:
                session = self._create_imported_session(human_thread_id, run_record)
            before = len(session.runs)
            session = session.with_run(run_record)
            self.catalog.save(session)
            sessions_by_thread_id[human_thread_id] = session
            imported += int(len(session.runs) > before)
        return imported

    def _save_run(self, session_id: str, run: TaskSessionRun) -> TaskSession:
        session = self.require(session_id).with_run(run)
        self.catalog.save(session)
        return session

    def _read_existing_run(
        self,
        run_dir: Path,
    ) -> tuple[str, TaskSessionRun] | None:
        manifest_path = run_dir / "run_manifest.json"
        request_path = run_dir / "run_request.json"
        if not manifest_path.is_file() or not request_path.is_file():
            return None
        manifest = read_run_manifest(manifest_path)
        request_payload = self._read_object(request_path)
        raw_request = request_payload.get("request")
        request = raw_request if isinstance(raw_request, Mapping) else {}
        checkpoint_path = Path(latest_checkpoint_path(str(run_dir)))
        checkpoint = load_task_checkpoint(str(checkpoint_path))
        metadata = checkpoint.metadata if isinstance(checkpoint.metadata, dict) else {}
        human_thread_id = str(
            request.get("human_thread_id")
            or metadata.get("human_thread_id")
            or self._legacy_thread_id(manifest.task)
        )
        resume_state = str(request.get("resume_state") or "")
        parent_run_id = self._parent_run_id(run_dir, resume_state)
        return human_thread_id, TaskSessionRun(
            run_id=manifest.run_id,
            task=manifest.task,
            artifact_dir=str(run_dir.resolve()),
            workspace=self._requested_workspace(checkpoint.to_dict()),
            checkpoint_path=str(checkpoint_path.resolve()),
            status=manifest.status,
            stop_reason=manifest.stop_reason,
            current_step=checkpoint.current_step,
            relationship="continuation" if resume_state else "initial",
            parent_run_id=parent_run_id,
            created_at=checkpoint.created_at or run_dir.stat().st_mtime,
            updated_at=checkpoint.updated_at or run_dir.stat().st_mtime,
        )

    def _create_imported_session(
        self,
        human_thread_id: str,
        first_run: TaskSessionRun,
    ) -> TaskSession:
        safe_id = self._stable_import_id(human_thread_id)
        existing = self.catalog.get(safe_id)
        if existing is not None:
            return existing
        session = TaskSession(
            session_id=safe_id,
            human_thread_id=human_thread_id,
            title=self._normalize_title(first_run.task),
            initial_task=first_run.task,
            workspace=first_run.workspace,
            created_at=first_run.created_at,
            updated_at=first_run.updated_at,
        )
        self.catalog.save(session)
        return session

    @staticmethod
    def _parent_run_id(run_dir: Path, resume_state: str) -> str:
        link_path = run_dir / "resume_link.json"
        if link_path.is_file():
            payload = TaskSessionLibrary._read_object(link_path)
            return str(payload.get("previous_run_id") or "")
        if resume_state:
            try:
                return load_task_checkpoint(resume_state).run_id
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return ""
        return ""

    @staticmethod
    def _requested_workspace(checkpoint: Mapping[str, Any]) -> str:
        metadata = checkpoint.get("metadata")
        environment = metadata.get("execution_environment") if isinstance(metadata, dict) else None
        if isinstance(environment, dict) and environment.get("requested_workspace"):
            return str(Path(str(environment["requested_workspace"])).resolve())
        return str(Path(str(checkpoint.get("workspace") or ".")).resolve())

    @staticmethod
    def _read_object(path: Path) -> Mapping[str, Any]:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"JSON root must be an object: {path}")
        return payload

    @staticmethod
    def _stable_import_id(human_thread_id: str) -> str:
        digest = hashlib.sha256(human_thread_id.encode("utf-8")).hexdigest()[:12]
        return f"imported-{digest}"

    @staticmethod
    def _legacy_thread_id(task: str) -> str:
        """把没有线程身份的旧 Run 按任务归组，避免随机 ID 淹没人类会话列表。"""

        normalized_task = re.sub(r"\s+", " ", task).strip().lower()
        digest = hashlib.sha256(normalized_task.encode("utf-8")).hexdigest()[:16]
        return f"legacy-task-{digest}"

    @staticmethod
    def _normalize_title(value: str) -> str:
        title = re.sub(r"\s+", " ", value).strip()
        if not title:
            return "未命名会话"
        return title if len(title) <= 48 else f"{title[:47]}…"
