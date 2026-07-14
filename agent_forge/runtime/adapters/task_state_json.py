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
    """Filesystem-backed task-state store for resume/replay workflows.

    In a production service this would be a database table with transactional
    writes. JSON files are enough for this repository while still proving the
    architecture: state is explicit, inspectable, resumable, and separate from
    trace artifacts.
    """

    def __init__(self, root: str | Path = ".agent_forge/task_state") -> None:
        """Create the state directory without deleting previous runs."""

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        """Return the checkpoint path for one run id."""

        return self.root / f"{run_id}.json"

    # RUNTIME PORT: AgentLoop creates the first durable state for every run.
    def start(
        self,
        run_id: str,
        task: str,
        workspace: str,
        agent_name: str,
        metadata: JsonObject | None = None,
    ) -> TaskCheckpoint:
        """Create and persist the initial checkpoint for ``AgentLoop.run``.

        Read this to understand where resumable state begins. It returns the
        typed ``TaskCheckpoint`` used by trace and later transitions; ``save``
        is only the storage detail below this boundary.
        """

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

    # RUNTIME PORT: RunLifecycle persists an explicit state transition here.
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
        metadata: JsonObject | None = None,
        updated_at: float | None = None,
    ) -> TaskCheckpoint:
        """Apply and persist one explicit checkpoint transition.

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
            metadata=metadata,
            updated_at=updated_at,
        )
        self.save(checkpoint)
        return checkpoint

    def save(self, checkpoint: TaskCheckpoint) -> None:
        """Persist one checkpoint as readable JSON."""

        self.path_for(checkpoint.run_id).write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, run_id: str) -> TaskCheckpoint:
        """Load a checkpoint by id."""

        data = json.loads(self.path_for(run_id).read_text(encoding="utf-8"))
        return TaskCheckpoint(**data)

    @staticmethod
    def load_path(path: str | Path) -> TaskCheckpoint:
        """Load a checkpoint from an explicit JSON path."""

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
        """Return checkpoints newest first."""

        checkpoints = []
        for path in self.root.glob("*.json"):
            try:
                checkpoints.append(TaskCheckpoint(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(checkpoints, key=lambda item: item.updated_at, reverse=True)

    def resume_summary(self, run_id: str, max_chars: int = 1400) -> str:
        """Build a compact context seed for a continuation run."""

        checkpoint = self.load(run_id)
        return summarize_checkpoint(checkpoint, max_chars=max_chars)

# Backward-compatible name for existing integrations. New wiring uses the
# implementation-specific repository name above.
TaskStateStore = JsonTaskStateRepository
