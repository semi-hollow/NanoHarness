"""Task Checkpoint 的 canonical JSON Repository。

系统角色：保存一次 Run attempt 的最新生命周期快照，供 continuation 定位和恢复；状态
迁移合法性仍由 Domain ``TaskCheckpoint`` 拥有。
输入：Start request / typed update；输出：原子持久化的 checkpoint。

折叠导航：1 create/update；2 load/latest；3 list。
"""

import json
from pathlib import Path

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.ports.repositories import TaskStateRepository


class JsonTaskStateRepository(TaskStateRepository):
    def __init__(
        self,
        root: str | Path = ".agent_forge/runs/embedded/task_state",
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    # region 1. Create / Update：Domain 迁移后以完整快照落盘
    # 运行时端口：创建 run 的首个 checkpoint 并写入 JSON。
    def start(self, request: TaskStartRequest) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            run_id=request.run_id,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            context_revision=request.context_revision,
            workspace=str(Path(request.workspace).resolve()),
            execution_workspace=str(Path(request.execution_workspace).resolve()),
            execution_mode=request.execution_mode,
            agent_name=request.agent_name,
            status=TaskRunStatus.CREATED.value,
            resume_hint="Resume this checkpoint in the same Thread and Turn.",
            metadata=request.metadata,
        )
        self.save(checkpoint)
        return checkpoint

    # 运行时端口：应用显式状态转换并覆盖同一 run 的 checkpoint。
    def update(
        self,
        checkpoint: TaskCheckpoint,
        update: TaskCheckpointUpdate,
    ) -> TaskCheckpoint:
        """应用并持久化一次显式 checkpoint 状态迁移。

        ``RunLifecycle.update_checkpoint`` 在 model、tool、pause 和 stop 后调用这里。
        显式关键字参数就是完整可变字段表，读者无需再进入 ``save`` 或 ``_write``。
        """

        checkpoint.apply_transition(update)
        self.save(checkpoint)
        return checkpoint

    def save(self, checkpoint: TaskCheckpoint) -> None:
        """用完整快照覆盖同一 run 的 ``<run_id>.json``。

        状态合法性由 ``TaskCheckpoint.apply_transition`` 校验；本方法只序列化单个文件，
        不计算状态迁移。``atomic_write_json`` 保证单文件替换与目录项落盘。
        """

        atomic_write_json(self.path_for(checkpoint.run_id), checkpoint.to_dict())
    # endregion 1. Create / Update 结束

    # region 2. Load / Latest：恢复只读取 canonical schema
    def load(self, run_id: str) -> TaskCheckpoint:
        data = json.loads(self.path_for(run_id).read_text(encoding="utf-8"))
        return TaskCheckpoint.from_dict(data)

    @staticmethod
    def load_path(path: str | Path) -> TaskCheckpoint:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return TaskCheckpoint.from_dict(data)

    @staticmethod
    def latest_path(run_dir: str | Path) -> Path:
        """返回一个 run 目录中更新时间最新的 checkpoint 文件。"""

        state_dir = Path(run_dir) / "task_state"
        candidates = sorted(state_dir.glob("*.json"))
        if not candidates:
            raise FileNotFoundError(f"no task_state checkpoints found under {run_dir}")

        # 优先使用 payload 的业务更新时间；坏文件仅在导航场景回退文件 mtime。
        def updated_at(path: Path) -> float:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return float(data.get("updated_at") or 0.0)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return path.stat().st_mtime

        return max(candidates, key=updated_at)
    # endregion 2. Load / Latest 结束

    # region 3. 控制面列表：隔离单个坏文件
    def list(self) -> list[TaskCheckpoint]:
        checkpoints = []
        for path in self.root.glob("*.json"):
            try:
                checkpoints.append(
                    TaskCheckpoint.from_dict(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                )
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return sorted(checkpoints, key=lambda item: item.updated_at, reverse=True)
    # endregion 3. 控制面列表结束
