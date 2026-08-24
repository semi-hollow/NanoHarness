"""Operation Ledger 的 crash-safe JSON Adapter。

系统角色：用稳定 operation identity 和 fingerprint 记录状态变更操作，
使 continuation 能区分“未执行”、“执行中结果不确定”与“已执行”。
输入：``OperationPlan`` / ``OperationTransition``；输出：持久化
``OperationRecord``。
相邻边界：``OperationTracker`` 决定复用/拒绝/执行；本 Adapter 只负责
身份、合法迁移、跨进程互斥与强原子落盘。

折叠导航：1 身份/fingerprint；2 公开状态迁移；3 锁内提交；4 路径 helper。
"""

from __future__ import annotations

import hashlib
import json
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.runtime.domain.operation import (
    OperationPlan,
    OperationRecord,
    OperationTarget,
    OperationTransition,
)
from agent_forge.runtime.ports.repositories import OperationLedgerRepository


class JsonOperationLedgerRepository(OperationLedgerRepository):
    """以 operation key 为文件名，持久化每项状态变更操作的最新状态和历史。

    仓储负责 fingerprint、合法状态迁移和 JSON 读写；是否复用结果、申请授权或执行工具
    由上层 Runtime 决定。每项操作独立落盘，不提供跨操作事务。
    """

    def __init__(
        self,
        root: str | Path = ".agent_forge/internal/state/operation_ledger",
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # region 1. 身份与 Fingerprint：跨 continuation 识别同一操作与目标漂移
    @staticmethod
    def operation_key(target: OperationTarget) -> str:
        """用规范化操作事实生成跨 continuation 稳定的幂等主键。

        ToolCall id 和 run id 都可能在恢复后变化，因此不参与 key；workspace 则必须参与，
        防止不同隔离目录中的同参数操作被错误视为同一次执行。
        """

        payload = {
            "tool_name": target.tool_name,
            "arguments": target.arguments,
            "workspace": str(Path(target.workspace).resolve()),
            "action": target.action,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def operation_fingerprint(target: OperationTarget) -> dict[str, Any]:
        """读取操作目标此刻的最小状态，用于审批和恢复时检测漂移。

        文件类操作记录解析路径及内容哈希；命令类操作只能记录命令文本；其他状态变更操作退化
        为参数哈希。Fingerprint 描述当前状态，不等于 operation key。
        """

        args = target.arguments
        root = Path(target.workspace).resolve()
        path_value = _target_path_value(args)
        if path_value:
            raw_path = Path(str(path_value))
            resolved = (
                raw_path if raw_path.is_absolute() else root / raw_path
            ).resolve()
            fingerprint: dict[str, Any] = {
                "kind": "path",
                "tool_name": target.tool_name,
                "action": target.action,
                "path": str(path_value),
                "resolved_path": str(resolved),
                "inside_workspace": _is_relative_to(resolved, root),
            }
            if (
                fingerprint["inside_workspace"]
                and resolved.exists()
                and resolved.is_file()
            ):
                content = resolved.read_bytes()
                fingerprint.update(
                    {
                        "exists": True,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                )
            else:
                fingerprint.update({"exists": False, "sha256": "", "size": 0})
            return fingerprint

        if target.action == "run_command" or target.tool_name == "run_command":
            return {
                "kind": "command",
                "tool_name": target.tool_name,
                "action": target.action,
                "command": str(args.get("command", "")),
            }

        raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        return {
            "kind": "operation",
            "tool_name": target.tool_name,
            "action": target.action,
            "arguments_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        }
    # endregion 1. 身份与 Fingerprint 结束

    # region 2. 公开状态迁移：Planned -> Pending/Approved -> Executing -> Terminal
    def path_for(self, operation_key: str) -> Path:
        return self.root / f"{operation_key}.json"

    def get(self, operation_key: str) -> OperationRecord | None:
        """按稳定 operation key 读取一条持久化执行记录。"""

        with self._operation_lock(operation_key):
            return self._get_unlocked(operation_key)

    def _get_unlocked(self, operation_key: str) -> OperationRecord | None:
        path = self.path_for(operation_key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return OperationRecord(**data)

    def record_pending(self, plan: OperationPlan) -> OperationRecord:
        """把 planned 记录迁移为等待人工批准。"""

        with self._operation_lock(plan.operation_key):
            existing = self._get_unlocked(plan.operation_key)
            if existing is not None and existing.status in {"executing", "executed"}:
                raise RuntimeError(
                    f"cannot move {existing.status} operation back to pending"
                )
            return self._record_unlocked(plan, existing=existing)

    def record_approved(self, update: OperationTransition) -> OperationRecord:
        """保存已授权状态，但不代表工具已经执行。"""

        with self._operation_lock(update.operation_key):
            record = self._require_unlocked(update.operation_key)
            if record.status in {"executing", "executed"}:
                raise RuntimeError(
                    f"cannot move {record.status} operation back to approved"
                )
            return self._transition_unlocked(record, update)

    def record_executing(self, update: OperationTransition) -> OperationRecord:
        """在状态变更操作调用前保存执行中状态。

        如果进程在工具返回后、最终结果提交前崩溃，这个状态会留在磁盘上。恢复流程
        因而知道结果不确定，不能把操作当作“尚未执行”而盲目重试。
        """

        with self._operation_lock(update.operation_key):
            record = self._require_unlocked(update.operation_key)
            if record.status != "approved":
                raise RuntimeError(
                    "state-changing operation execution claim rejected: "
                    f"current status is {record.status}"
                )
            return self._transition_unlocked(record, update)

    # 运行时端口：记录状态变更操作已执行及执行后的目标指纹。
    def record_executed(self, update: OperationTransition) -> OperationRecord:
        return self._finish_execution(update)

    # 运行时端口：记录状态变更操作失败，供恢复流程决定是否可重试。
    def record_failed(self, update: OperationTransition) -> OperationRecord:
        return self._finish_execution(update)

    # 运行时端口：首次见到 operation 时创建 planned 状态记录。
    def ensure_planned(self, plan: OperationPlan) -> OperationRecord:
        """返回已有操作记录，或持久化新的 planned 状态。

        ``ToolExecutionPipeline`` 在状态变更操作启动前调用这里。稳定 key 和 pre-fingerprint
        让 continuation 不再执行已完成操作，并在目标变化时拒绝复用旧结果。
        """

        with self._operation_lock(plan.operation_key):
            existing = self._get_unlocked(plan.operation_key)
            if existing is not None:
                # 旧记录缺 fingerprint 时只补证据，不重置它已有的执行状态。
                if existing.pre_fingerprint is None and plan.pre_fingerprint is not None:
                    existing.pre_fingerprint = plan.pre_fingerprint
                    self._write_unlocked(existing)
                return existing
            return self._record_unlocked(plan, existing=None)
    # endregion 2. 公开状态迁移结束

    # region 3. 锁内提交：所有 read-modify-write 都从这里落盘
    def _record_unlocked(
        self,
        plan: OperationPlan,
        *,
        existing: OperationRecord | None,
    ) -> OperationRecord:
        """在已持有 operation lock 时创建记录，或推进已有记录。"""

        # 同 key 重入只能经过 Domain transition，不能用新对象覆盖 history。
        if existing is not None:
            return self._transition_unlocked(
                existing,
                OperationTransition(
                    operation_key=plan.operation_key,
                    status=plan.status,
                    run_id=plan.run_id,
                    step=plan.step,
                    pre_fingerprint=plan.pre_fingerprint,
                ),
            )
        record = OperationRecord(
            operation_key=plan.operation_key,
            status=plan.status,
            tool_name=plan.target.tool_name,
            arguments=plan.target.arguments,
            action=plan.target.action,
            workspace=str(Path(plan.target.workspace).resolve()),
            run_id=plan.run_id,
            step=plan.step,
            history=[plan.status],
            pre_fingerprint=plan.pre_fingerprint,
        )
        self._write_unlocked(record)
        return record

    def _transition_unlocked(
        self,
        record: OperationRecord,
        update: OperationTransition,
    ) -> OperationRecord:
        record.transition(update)
        self._write_unlocked(record)
        return record

    def _require_unlocked(self, operation_key: str) -> OperationRecord:
        record = self._get_unlocked(operation_key)
        if record is None:
            raise FileNotFoundError(f"operation record not found: {operation_key}")
        return record

    def _finish_execution(self, update: OperationTransition) -> OperationRecord:
        with self._operation_lock(update.operation_key):
            record = self._require_unlocked(update.operation_key)
            # 只有已 claim 的 executing 记录，才能提交成功或失败结果。
            if record.status != "executing":
                raise RuntimeError(
                    f"cannot record {update.status} from operation status {record.status}"
                )
            if record.run_id != update.run_id or record.step != update.step:
                raise RuntimeError(
                    "only the executing claimant may commit an operation result"
                )
            return self._transition_unlocked(record, update)

    def _write_unlocked(self, record: OperationRecord) -> None:
        """将一个 operation 的最新状态及完整 history 写入独立 JSON 文件。"""

        record.path = str(self.path_for(record.operation_key))
        atomic_write_json(self.path_for(record.operation_key), record.to_dict())

    @contextmanager
    def _operation_lock(self, operation_key: str) -> Iterator[None]:
        """按 operation key 串行化跨进程 read-modify-write 与 executing claim。"""

        lock_directory = self.root / ".locks"
        lock_directory.mkdir(parents=True, exist_ok=True)
        lock_path = lock_directory / f"{operation_key}.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    # endregion 3. 锁内提交结束


# region 4. Fingerprint 路径 helper
def _target_path_value(arguments: dict[str, Any]) -> Any:
    # 工具 schema 的路径字段名不同，但 fingerprint 只选第一个真实目标。
    for key in ("path", "file", "target_path", "output_path"):
        value = arguments.get(key)
        if value:
            return value
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
# endregion 4. Fingerprint 路径 helper 结束
