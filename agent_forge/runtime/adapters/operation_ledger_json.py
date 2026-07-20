from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent_forge.runtime.domain.operation import (
    OperationPlan,
    OperationRecord,
    OperationTarget,
    OperationTransition,
)
from agent_forge.runtime.ports.repositories import OperationLedgerRepository


class JsonOperationLedgerRepository(OperationLedgerRepository):
    def __init__(self, root: str | Path = ".agent_forge/operation_ledger") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def operation_key(target: OperationTarget) -> str:
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

    def path_for(self, operation_key: str) -> Path:
        return self.root / f"{operation_key}.json"

    def get(self, operation_key: str) -> OperationRecord | None:
        path = self.path_for(operation_key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return OperationRecord(**data)

    def record_pending(self, plan: OperationPlan) -> OperationRecord:
        return self._record(plan)

    def record_approved(self, update: OperationTransition) -> OperationRecord:
        return self._transition(self._require(update.operation_key), update)

    # 运行时端口：记录副作用已执行及执行后的目标指纹。
    def record_executed(self, update: OperationTransition) -> OperationRecord:
        return self._transition(self._require(update.operation_key), update)

    # 运行时端口：记录副作用失败，供恢复流程决定是否可重试。
    def record_failed(self, update: OperationTransition) -> OperationRecord:
        return self._transition(self._require(update.operation_key), update)

    # 运行时端口：首次见到 operation 时创建 planned 账本记录。
    def ensure_planned(self, plan: OperationPlan) -> OperationRecord:
        """返回已有操作记录，或持久化新的 planned 状态。

        ``ToolExecutionPipeline`` 在副作用前调用这里。稳定 key 和 pre-fingerprint
        让 continuation 跳过已完成操作，并拒绝盲目重放已变化的目标。
        """

        existing = self.get(plan.operation_key)
        if existing is not None:
            if existing.pre_fingerprint is None and plan.pre_fingerprint is not None:
                existing.pre_fingerprint = plan.pre_fingerprint
                self._write(existing)
            return existing
        return self._record(plan)

    def _record(self, plan: OperationPlan) -> OperationRecord:
        existing = self.get(plan.operation_key)
        if existing is not None:
            return self._transition(
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
        self._write(record)
        return record

    def _transition(
        self,
        record: OperationRecord,
        update: OperationTransition,
    ) -> OperationRecord:
        record.transition(update)
        self._write(record)
        return record

    def _require(self, operation_key: str) -> OperationRecord:
        record = self.get(operation_key)
        if record is None:
            raise FileNotFoundError(f"operation record not found: {operation_key}")
        return record

    def _write(self, record: OperationRecord) -> None:
        record.path = str(self.path_for(record.operation_key))
        self.path_for(record.operation_key).write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


def _target_path_value(arguments: dict[str, Any]) -> Any:
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
