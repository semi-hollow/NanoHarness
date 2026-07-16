import json
from pathlib import Path

from agent_forge.contracts import JsonObject
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointData,
    TaskRunStatus,
    summarize_checkpoint,
)


class JsonTaskStateRepository:

    def __init__(self, root: str | Path = ".agent_forge/task_state") -> None:

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:

        return self.root / f"{run_id}.json"

    # 运行时端口：下方定义连接用例与外部实现。
    def start(
        self,
        run_id: str,
        task: str,
        workspace: str,
        agent_name: str,
        metadata: JsonObject | None = None,
    ) -> TaskCheckpoint:

        checkpoint = TaskCheckpoint(
            run_id=run_id,
            task=task,
            workspace=str(Path(workspace).resolve()),
            agent_name=agent_name,
            status=TaskRunStatus.CREATED.value,
            resume_hint="Run with --resume-state this_id to seed a continuation from this checkpoint.",
            metadata=metadata or {},
        )
        self.save(checkpoint)
        return checkpoint

    # 运行时端口：下方定义连接用例与外部实现。
    def update(
        self,
        checkpoint: TaskCheckpoint,
        *,
        status: str | None = None,
        current_step: int | None = None,
        last_tool: str | None = None,
        last_observation: str | None = None,
        stop_reason: str | None = None,
        final_answer: str | None = None,
        resume_hint: str | None = None,
        messages_count: int | None = None,
        observations_count: int | None = None,
        context_digest: JsonObject | None = None,
        metadata: JsonObject | None = None,
        updated_at: float | None = None,
    ) -> TaskCheckpoint:
        """应用并持久化一次显式 checkpoint 状态迁移。

        ``RunLifecycle.update`` 在 model、tool、pause 和 stop 后调用这里。显式关键字
        参数就是完整可变字段表，读者无需再进入 ``save`` 或 ``_write``。
        """

        checkpoint.apply_transition(
            status=status,
            current_step=current_step,
            last_tool=last_tool,
            last_observation=last_observation,
            stop_reason=stop_reason,
            final_answer=final_answer,
            resume_hint=resume_hint,
            messages_count=messages_count,
            observations_count=observations_count,
            context_digest=context_digest,
            metadata=metadata,
            updated_at=updated_at,
        )
        self.save(checkpoint)
        return checkpoint

    def save(self, checkpoint: TaskCheckpoint) -> None:

        self.path_for(checkpoint.run_id).write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, run_id: str) -> TaskCheckpoint:

        data = json.loads(self.path_for(run_id).read_text(encoding="utf-8"))
        return TaskCheckpoint(**data)

    @staticmethod
    def load_path(path: str | Path) -> TaskCheckpoint:

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return TaskCheckpoint(**data)

    @staticmethod
    def latest_path(run_dir: str | Path) -> Path:
        """返回一个 run 目录中更新时间最新的 checkpoint 文件。"""

        state_dir = Path(run_dir) / "task_state"
        candidates = sorted(state_dir.glob("*.json"))
        if not candidates:
            raise FileNotFoundError(
                f"no task_state checkpoints found under {run_dir}"
            )

        def updated_at(path: Path) -> float:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return float(data.get("updated_at") or 0.0)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return path.stat().st_mtime

        return max(candidates, key=updated_at)

    def list(self) -> list[TaskCheckpoint]:

        checkpoints = []
        for path in self.root.glob("*.json"):
            try:
                checkpoints.append(TaskCheckpoint(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(checkpoints, key=lambda item: item.updated_at, reverse=True)

    def resume_summary(self, run_id: str, max_chars: int = 1400) -> str:

        checkpoint = self.load(run_id)
        return summarize_checkpoint(checkpoint, max_chars=max_chars)
