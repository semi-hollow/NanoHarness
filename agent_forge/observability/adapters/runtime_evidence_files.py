"""为每次 Runtime 运行发布稳定、可直接阅读的控制面证据入口。

Checkpoint、Approval、Human Input 和 Operation Ledger 仍由各自 Repository 保存。
本模块只创建按 run 分类的软链接和索引，不复制或改写权威状态，避免恢复时出现两个事实源。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, kw_only=True)
class _EvidenceGroup:
    """一个阅读分组的固定目录名和语义。"""

    group_id: str
    directory: str
    title: str
    meaning: str
    cardinality: str


@dataclass(frozen=True, kw_only=True)
class _EvidenceFile:
    """一项权威文件及其在分类视图中的链接信息。"""

    source: Path
    link_name: str
    status: str = ""
    identity: str = ""


_GROUPS = (
    _EvidenceGroup(
        group_id="checkpoint",
        directory="01_checkpoint",
        title="Checkpoint",
        meaning="恢复整次 run 的消息、Observation、当前 Step 和停止原因。",
        cardinality="每个 run 一个 JSON；状态变化时覆盖同一文件。",
    ),
    _EvidenceGroup(
        group_id="human_input",
        directory="02_human_input",
        title="Human Input",
        meaning="保存 Agent 提问、人工回答和 WAITING_HUMAN 生命周期。",
        cardinality="每个 request_id 一个 JSON；回答或取消时覆盖同一文件。",
    ),
    _EvidenceGroup(
        group_id="approval",
        directory="03_approval",
        title="Approval",
        meaning="保存某个具体副作用操作是否获得人工授权。",
        cardinality="每个 operation_key 一个 JSON；批准、拒绝或失效时覆盖同一文件。",
    ),
    _EvidenceGroup(
        group_id="operation_ledger",
        directory="04_operation_ledger",
        title="Operation Ledger",
        meaning="保存副作用操作执行到 planned、approved、executing、executed 或 failed。",
        cardinality="每个 operation_key 一个 JSON；最新状态覆盖，history 保留迁移过程。",
    ),
    _EvidenceGroup(
        group_id="trace",
        directory="05_trace",
        title="Trace",
        meaning="保存本次 run 已发生的模型、工具、治理和状态转换事件。",
        cardinality="每个 run 一个 JSON；终态时一次发布完整事件数组。",
    ),
    _EvidenceGroup(
        group_id="run_artifacts",
        directory="06_run_artifacts",
        title="Run Artifacts",
        meaning="保存请求、配置、候选 Diff、Usage、环境和恢复链等本次运行产物。",
        cardinality="每个 run 一组文件；各文件只表达自己的证据边界。",
    ),
)

_RUN_ARTIFACT_NAMES = (
    "run_manifest.json",
    "run_request.json",
    "resolved_config.json",
    "execution_environment.json",
    "practice_profile.json",
    "candidate_changes.diff",
    "final_answer.txt",
    "usage.json",
    "usage_report.md",
    "resume_link.json",
    "resume_chain.md",
)


# 主要入口：为一次已落盘的 run 创建独立证据目录，并原子更新 latest 快捷入口。
def publish_runtime_evidence_view(
    *,
    workspace: str | Path,
    run_dir: str | Path,
    approval_root: str | Path,
    human_input_root: str | Path,
    operation_ledger_root: str | Path,
) -> Path:
    """发布按 run 分类的只读导航视图，返回该 run 的独立证据目录。

    ``runs/<run-name>`` 永久保留本次索引，后续运行不会覆盖；``latest`` 只是指向
    最新视图的软链接。分类目录中的文件也都是软链接，因此审批、回答和账本状态更新后，
    打开视图看到的仍是 Repository 中的最新权威内容。
    """

    requested_workspace = Path(workspace).expanduser().resolve()
    artifact_dir = Path(run_dir).expanduser().resolve()
    if not artifact_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {artifact_dir}")

    # 先读取 run-local 事实，再用 run_id 和 Trace 中的稳定 ID 关联工作区控制面文件。
    manifest = _read_json_object(artifact_dir / "run_manifest.json")
    trace = _read_json_object(artifact_dir / "trace.json")
    checkpoint_files = sorted((artifact_dir / "task_state").glob("*.json"))
    checkpoint_payloads = [
        payload
        for path in checkpoint_files
        if (payload := _read_json_object(path)) is not None
    ]
    run_id = _first_text(
        manifest,
        "run_id",
        fallback=_first_text(trace, "run_id", fallback=_checkpoint_run_id(checkpoint_payloads)),
    )
    task = _first_text(
        manifest,
        "task",
        fallback=_first_text(trace, "task", fallback=_checkpoint_task(checkpoint_payloads)),
    )
    status = _first_text(manifest, "status", fallback=_checkpoint_status(checkpoint_payloads))
    stop_reason = _first_text(
        manifest,
        "stop_reason",
        fallback=_first_text(trace, "stop_reason", fallback=_checkpoint_stop_reason(checkpoint_payloads)),
    )

    operation_keys, request_ids = _collect_control_identifiers(
        trace,
        *checkpoint_payloads,
    )
    approval_files = _select_control_files(
        Path(approval_root),
        run_id=run_id,
        referenced_ids=operation_keys,
        identity_field="operation_key",
    )
    human_input_files = _select_control_files(
        Path(human_input_root),
        run_id=run_id,
        referenced_ids=request_ids,
        identity_field="request_id",
    )
    ledger_files = _select_control_files(
        Path(operation_ledger_root),
        run_id=run_id,
        referenced_ids=operation_keys,
        identity_field="operation_key",
    )

    evidence_root = requested_workspace / ".agent_forge" / "runtime_evidence"
    runs_root = evidence_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    view_name = _view_name(artifact_dir.name, task)
    final_view = runs_root / view_name
    temporary_view = runs_root / f".{view_name}.{uuid.uuid4().hex}.tmp"
    temporary_view.mkdir(parents=True, exist_ok=False)

    grouped_files = {
        "checkpoint": _checkpoint_evidence_files(checkpoint_files),
        "human_input": human_input_files,
        "approval": approval_files,
        "operation_ledger": ledger_files,
        "trace": (
            [_EvidenceFile(source=artifact_dir / "trace.json", link_name="trace.json")]
            if (artifact_dir / "trace.json").is_file()
            else []
        ),
        "run_artifacts": [
            _EvidenceFile(source=artifact_dir / name, link_name=name)
            for name in _RUN_ARTIFACT_NAMES
            if (artifact_dir / name).is_file()
        ],
    }

    try:
        index_groups: list[dict[str, Any]] = []
        for group in _GROUPS:
            group_dir = temporary_view / group.directory
            group_dir.mkdir()
            records = []
            for evidence_file in grouped_files[group.group_id]:
                link = group_dir / evidence_file.link_name
                _create_relative_symlink(evidence_file.source, link)
                records.append(
                    {
                        "link": f"{group.directory}/{evidence_file.link_name}",
                        "source": str(evidence_file.source.resolve()),
                        "status": evidence_file.status,
                        "identity": evidence_file.identity,
                    }
                )
            index_groups.append(
                {
                    "id": group.group_id,
                    "directory": group.directory,
                    "title": group.title,
                    "meaning": group.meaning,
                    "cardinality": group.cardinality,
                    "files": records,
                }
            )

        index_payload = {
            "schema_version": 1,
            "generated_at": time.time(),
            "run": {
                "run_id": run_id,
                "task": task,
                "status": status,
                "stop_reason": stop_reason,
                "artifact_dir": str(artifact_dir),
                "workspace": str(requested_workspace),
            },
            "groups": index_groups,
        }
        (temporary_view / "index.json").write_text(
            json.dumps(index_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (temporary_view / "README.md").write_text(
            _render_run_readme(index_payload),
            encoding="utf-8",
        )

        _remove_generated_path(final_view)
        temporary_view.replace(final_view)
    finally:
        if temporary_view.exists():
            shutil.rmtree(temporary_view)

    _update_latest_link(evidence_root, final_view)
    _write_root_readme(evidence_root)
    _write_run_catalog(evidence_root)
    return final_view


def _select_control_files(
    root: Path,
    *,
    run_id: str,
    referenced_ids: set[str],
    identity_field: str,
) -> list[_EvidenceFile]:
    """只选择本次 run 创建或在 Trace/Checkpoint 中引用的控制面记录。"""

    if not root.is_dir():
        return []
    selected: list[_EvidenceFile] = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json_object(path)
        if payload is None:
            continue
        identity = str(payload.get(identity_field) or path.stem)
        belongs_to_run = bool(run_id) and str(payload.get("run_id") or "") == run_id
        if not belongs_to_run and identity not in referenced_ids:
            continue
        selected.append(
            _EvidenceFile(
                source=path.resolve(),
                link_name=path.name,
                status=str(payload.get("status") or ""),
                identity=identity,
            )
        )
    return selected


def _checkpoint_evidence_files(paths: list[Path]) -> list[_EvidenceFile]:
    """单 checkpoint 使用固定名称；异常多文件时保留原名以暴露事实。"""

    if len(paths) == 1:
        payload = _read_json_object(paths[0]) or {}
        return [
            _EvidenceFile(
                source=paths[0].resolve(),
                link_name="checkpoint.json",
                status=str(payload.get("status") or ""),
                identity=str(payload.get("run_id") or paths[0].stem),
            )
        ]
    return [
        _EvidenceFile(
            source=path.resolve(),
            link_name=path.name,
            status=str((_read_json_object(path) or {}).get("status") or ""),
            identity=path.stem,
        )
        for path in paths
    ]


def _collect_control_identifiers(*payloads: Mapping[str, Any] | None) -> tuple[set[str], set[str]]:
    """递归提取 Trace/Checkpoint 已引用的 operation key 和 human request id。"""

    operation_keys: set[str] = set()
    request_ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key == "operation_key" and isinstance(nested, str) and nested:
                    operation_keys.add(nested)
                if key in {"request_id", "human_input_request_id"} and isinstance(nested, str) and nested:
                    request_ids.add(nested)
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for payload in payloads:
        if payload is not None:
            visit(payload)
    return operation_keys, request_ids


def _render_run_readme(index: Mapping[str, Any]) -> str:
    run = index["run"]
    groups = index["groups"]
    lines = [
        f"# Runtime 控制证据：{run['run_id'] or '未记录 run id'}",
        "",
        "> 本目录由运行时自动生成。分类文件都是指向权威持久化文件的软链接，",
        "> 不是副本；不要在这里创建第二份 Checkpoint、Approval 或 Ledger。",
        "",
        f"- **任务：** {run['task'] or '未记录'}",
        f"- **状态：** {run['status'] or '未记录'}",
        f"- **停止原因：** {run['stop_reason'] or '未记录'}",
        f"- **原始 Run 目录：** `{run['artifact_dir']}`",
        "",
        "## 推荐阅读顺序",
        "",
        "```text",
        "ToolCall",
        "  -> operation_key（同一次操作意图）",
        "  -> Approval（是否获准）",
        "  -> Checkpoint（暂停位置）",
        "  -> Resume 后重验 fingerprint（目标是否漂移）",
        "  -> Operation Ledger（副作用是否已经执行）",
        "  -> ToolGateway（真实执行）",
        "  -> Trace（全过程审计）",
        "```",
        "",
        "## 本次文件",
        "",
        "| 顺序 | 类型 | 数量 | 文件规则 | 作用 |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for group in groups:
        lines.append(
            f"| `{group['directory']}` | {group['title']} | {len(group['files'])} | "
            f"{group['cardinality']} | {group['meaning']} |"
        )
    lines.extend(
        [
            "",
            "没有 Approval 或 Human Input 文件，表示本次 run 没有触发对应机制，",
            "不表示项目缺少该能力。机器可读的来源和状态见 [`index.json`](index.json)。",
            "",
            "## 证据边界",
            "",
            "- Checkpoint 证明可恢复业务状态，不恢复 Python 调用栈或模型 KV Cache。",
            "- Approval 证明人授权了具体操作，不证明工具已经执行。",
            "- Operation Ledger 证明副作用状态与历史，不提供跨操作数据库事务。",
            "- Candidate Diff 只证明产生候选改动，不等于测试通过或 official resolved。",
            "- Trace 是审计事实流；Workbench 是它的可读投影，不是新的事实源。",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_root_readme(root: Path) -> None:
    content = """# Runtime Evidence

这里是运行时自动维护的控制面证据导航，不是第二套状态仓储。

- `latest/`：最近一次运行的快捷入口。
- `runs/`：每次运行各自独立的证据视图，后续运行不会覆盖历史视图。
- `INDEX.md`：按时间列出已有运行，便于调试或演示时切换。

每个视图都按 Checkpoint、Human Input、Approval、Operation Ledger、Trace 和
Run Artifacts 分类。目录中的业务文件均为软链接，权威源仍在原始 run/control store。
"""
    _atomic_write_text(root / "README.md", content)


def _write_run_catalog(root: Path) -> None:
    entries: list[tuple[float, str, str, str, str]] = []
    for index_path in (root / "runs").glob("*/index.json"):
        payload = _read_json_object(index_path)
        if payload is None or not isinstance(payload.get("run"), Mapping):
            continue
        run = payload["run"]
        entries.append(
            (
                float(payload.get("generated_at") or 0.0),
                index_path.parent.name,
                str(run.get("status") or "未记录"),
                str(run.get("task") or "未记录").replace("\n", " "),
                str(run.get("run_id") or "未记录"),
            )
        )
    entries.sort(reverse=True)
    lines = [
        "# Runtime 运行证据索引",
        "",
        "最近一次直接打开 [`latest/README.md`](latest/README.md)。历史运行按下表切换：",
        "",
        "| 运行目录 | 状态 | Task | Run ID |",
        "| --- | --- | --- | --- |",
    ]
    for _, name, status, task, run_id in entries:
        safe_task = task.replace("|", "\\|")
        lines.append(f"| [`{name}`](runs/{name}/README.md) | {status} | {safe_task} | `{run_id}` |")
    _atomic_write_text(root / "INDEX.md", "\n".join(lines) + "\n")


def _update_latest_link(root: Path, target: Path) -> None:
    latest = root / "latest"
    temporary = root / f".latest.{uuid.uuid4().hex}.tmp"
    temporary.symlink_to(Path("runs") / target.name, target_is_directory=True)
    try:
        if latest.exists() and not latest.is_symlink():
            _remove_generated_path(latest)
        os.replace(temporary, latest)
    finally:
        temporary.unlink(missing_ok=True)


def _create_relative_symlink(source: Path, link: Path) -> None:
    relative_target = os.path.relpath(source.resolve(), start=link.parent.resolve())
    link.symlink_to(relative_target)


def _remove_generated_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _first_text(payload: Mapping[str, Any] | None, key: str, *, fallback: str = "") -> str:
    if payload is None:
        return fallback
    value = payload.get(key)
    return str(value) if value is not None and value != "" else fallback


def _checkpoint_run_id(payloads: Iterable[Mapping[str, Any]]) -> str:
    return next((str(item.get("run_id")) for item in payloads if item.get("run_id")), "")


def _checkpoint_task(payloads: Iterable[Mapping[str, Any]]) -> str:
    return next((str(item.get("task")) for item in payloads if item.get("task")), "")


def _checkpoint_status(payloads: Iterable[Mapping[str, Any]]) -> str:
    return next((str(item.get("status")) for item in payloads if item.get("status")), "")


def _checkpoint_stop_reason(payloads: Iterable[Mapping[str, Any]]) -> str:
    return next((str(item.get("stop_reason")) for item in payloads if item.get("stop_reason")), "")


def _view_name(run_directory_name: str, task: str) -> str:
    task_slug = re.sub(r"[^\w.-]+", "-", task.strip(), flags=re.UNICODE).strip("-._")[:48]
    base = re.sub(r"[^\w.-]+", "-", run_directory_name, flags=re.UNICODE).strip("-._")
    return f"{base}__{task_slug}" if task_slug else base


__all__ = ["publish_runtime_evidence_view"]
