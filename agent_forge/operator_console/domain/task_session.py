"""人类可导航的任务会话模型，不包含文件系统读写。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping


@dataclass(frozen=True, kw_only=True)
class TaskSessionRun:
    """一个 Task Session 中的一次不可变运行记录。

    Session 用于人类导航；Run 仍保留独立随机 ID 和 artifact 目录。一个恢复、
    后续追问或重新尝试都会产生新的本对象，不会覆盖历史证据。
    """

    run_id: str
    task: str
    artifact_dir: str
    workspace: str
    checkpoint_path: str
    status: str
    stop_reason: str
    current_step: int
    relationship: str
    parent_run_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "artifact_dir": self.artifact_dir,
            "workspace": self.workspace,
            "checkpoint_path": self.checkpoint_path,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "current_step": self.current_step,
            "relationship": self.relationship,
            "parent_run_id": self.parent_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskSessionRun":
        return cls(
            run_id=str(value.get("run_id") or ""),
            task=str(value.get("task") or ""),
            artifact_dir=str(value.get("artifact_dir") or ""),
            workspace=str(value.get("workspace") or ""),
            checkpoint_path=str(value.get("checkpoint_path") or ""),
            status=str(value.get("status") or "unknown"),
            stop_reason=str(value.get("stop_reason") or ""),
            current_step=int(value.get("current_step") or 0),
            relationship=str(value.get("relationship") or "run"),
            parent_run_id=str(value.get("parent_run_id") or ""),
            created_at=float(value.get("created_at") or 0.0),
            updated_at=float(value.get("updated_at") or 0.0),
        )


@dataclass(frozen=True, kw_only=True)
class TaskSession:
    """跨多个 Run 的人类可读任务会话。

    ``session_id`` 是本地目录身份；``human_thread_id`` 会写进每次 checkpoint，
    让恢复和后续追问保持同一人工交互线程。``runs`` 只保存索引，真实 Trace、Diff、
    Usage 和 Checkpoint 仍由各自 artifact 目录负责。
    """

    session_id: str
    human_thread_id: str
    title: str
    initial_task: str
    workspace: str
    created_at: float
    updated_at: float
    archived: bool = False
    pinned: bool = False
    runs: tuple[TaskSessionRun, ...] = field(default_factory=tuple)
    schema_version: int = 1

    @property
    def latest_run(self) -> TaskSessionRun | None:
        """返回最近更新的 Run；没有执行记录时返回 ``None``。"""

        return max(self.runs, key=lambda item: item.updated_at, default=None)

    def with_run(self, run: TaskSessionRun) -> "TaskSession":
        """幂等加入一条 Run 索引，并刷新会话时间与当前 workspace。"""

        runs_by_id = {item.run_id: item for item in self.runs}
        runs_by_id[run.run_id] = run
        ordered_runs = tuple(
            sorted(runs_by_id.values(), key=lambda item: (item.created_at, item.run_id))
        )
        return replace(
            self,
            workspace=run.workspace or self.workspace,
            updated_at=max(self.updated_at, run.updated_at),
            runs=ordered_runs,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "human_thread_id": self.human_thread_id,
            "title": self.title,
            "initial_task": self.initial_task,
            "workspace": self.workspace,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived": self.archived,
            "pinned": self.pinned,
            "runs": [run.to_dict() for run in self.runs],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskSession":
        raw_runs = value.get("runs")
        runs = (
            tuple(
                TaskSessionRun.from_dict(item)
                for item in raw_runs
                if isinstance(item, Mapping)
            )
            if isinstance(raw_runs, list)
            else ()
        )
        return cls(
            schema_version=int(value.get("schema_version") or 1),
            session_id=str(value.get("session_id") or ""),
            human_thread_id=str(value.get("human_thread_id") or ""),
            title=str(value.get("title") or "未命名会话"),
            initial_task=str(value.get("initial_task") or ""),
            workspace=str(value.get("workspace") or ""),
            created_at=float(value.get("created_at") or 0.0),
            updated_at=float(value.get("updated_at") or 0.0),
            archived=bool(value.get("archived", False)),
            pinned=bool(value.get("pinned", False)),
            runs=runs,
        )
