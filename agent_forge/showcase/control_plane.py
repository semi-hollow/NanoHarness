"""HITL 与副作用审批的可重复控制面展示。

这里仅用确定性 ``ModelPort`` 固定模型会提出什么工具请求；暂停、持久化、审批、
checkpoint、恢复和文件修改全部经过正式 Runtime。这样现场演示不依赖模型随机性，
也不会把测试替身误说成完整能力。
"""

from __future__ import annotations

import json
import shlex
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agent_forge.contracts import ToolSchema
from agent_forge.harness import Harness, HarnessConfig, RunRequest
from agent_forge.runtime.api import (
    HumanInputResponseCommand,
    ToolRegistryBuildRequest,
    decide_approval,
    list_pending_approvals,
    list_pending_human_inputs,
    load_task_checkpoint,
    respond_to_human_input,
)
from agent_forge.runtime.domain.conversation import AgentResponse, Message, ToolCall
from agent_forge.runtime.wiring import build_registry

HITL_QUESTION = "Which compatibility target should be used?"
HITL_TASK = (
    "Implement the compatibility update in result.txt after asking the operator "
    "which API version should be targeted."
)
APPROVAL_TASK = "Implement the requested value update in target.py and verify the edit."


@dataclass(frozen=True)
class ControlPlaneShowcaseResult:
    """一次展示命令需要交给操作者的最小结果。"""

    scenario: str
    status: str
    run_dir: Path
    artifact_dir: Path
    workspace: Path
    checkpoint_path: Path
    trace_path: Path
    request_id: str = ""
    operation_key: str = ""
    next_command: str = ""


@dataclass(frozen=True)
class GovernedRunDemoResult:
    """一个命令完成的等待→人工决定→continuation 演示摘要。"""

    scenario: str
    run_dir: Path
    waiting_status: str
    completed_status: str
    report_path: Path
    inspect_target: Path


# 主要入口：用一个命令展示真实 Runtime 的人工屏障和 continuation。
def run_governed_demo(
    scenario: str = "approval",
    *,
    output_root: str | Path = ".agent_forge/showcases",
    answer: str = "Python 3.11",
) -> GovernedRunDemoResult:
    """串联两个正式 Runtime phase；确定性模型只固定工具意图。"""

    waiting = start_control_plane_showcase(scenario, output_root=output_root)
    completed = continue_control_plane_showcase(
        scenario,
        waiting.run_dir,
        answer=answer,
    )
    report_path = waiting.run_dir / "demo.md"
    report_path.write_text(
        _render_governed_demo(waiting, completed),
        encoding="utf-8",
    )
    _publish_default_demo_pointer(output_root, completed.artifact_dir)
    return GovernedRunDemoResult(
        scenario=scenario,
        run_dir=waiting.run_dir,
        waiting_status=waiting.status,
        completed_status=completed.status,
        report_path=report_path,
        inspect_target=completed.artifact_dir,
    )


def _publish_default_demo_pointer(output_root: str | Path, artifact_dir: Path) -> None:
    """让默认 CLI demo 可立即用 ``forge inspect latest`` 查看。"""

    if Path(output_root) != Path(".agent_forge/showcases"):
        return
    latest = Path(".agent_forge/latest")
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "run.txt").write_text(str(artifact_dir.resolve()), encoding="utf-8")


class _HitlShowcaseModel:
    """固定先提出同一个人工问题，再给出最终答案。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "showcase-ask-human",
                        "ask_human",
                        {
                            "question": HITL_QUESTION,
                            "choices": ["Python 3.10", "Python 3.11"],
                        },
                    )
                ],
            )
        return AgentResponse(
            "PASS\noperator response loaded; continuation completed", []
        )


class _ApprovalShowcaseModel:
    """固定提出同一个文件补丁，再在工具成功后结束。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "showcase-apply-patch",
                        "apply_patch",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    )
                ],
            )
        return AgentResponse(
            "PASS\napproved patch executed; continuation completed", []
        )


# 主要入口：创建一个真实停在 waiting_human / waiting_approval 的 Runtime run。
def start_control_plane_showcase(
    scenario: str,
    *,
    output_root: str | Path = ".agent_forge/showcases",
) -> ControlPlaneShowcaseResult:
    """启动展示并在人工控制点返回，不自动回答或批准。"""

    _validate_scenario(scenario)
    run_dir = _new_run_dir(output_root, scenario)
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    if scenario == "approval":
        (workspace / "target.py").write_text("value = 1\n", encoding="utf-8")

    result = _run_phase(
        scenario,
        run_dir=run_dir,
        workspace=workspace,
    )
    _write_showcase_artifacts(result)
    return result


# 主要入口：保存人工决定，并从上一阶段 checkpoint 启动显式 continuation。
def continue_control_plane_showcase(
    scenario: str,
    run_dir: str | Path,
    *,
    answer: str = "",
) -> ControlPlaneShowcaseResult:
    """回答 HITL 或批准副作用，然后重新进入正式 AgentLoop。"""

    _validate_scenario(scenario)
    root = Path(run_dir).resolve()
    manifest = _load_manifest(root)
    expected = manifest.get("scenario")
    if expected != scenario:
        raise ValueError(
            f"showcase scenario mismatch: expected {expected!r}, got {scenario!r}"
        )

    checkpoint_path = Path(str(manifest["checkpoint_path"]))
    checkpoint = load_task_checkpoint(str(checkpoint_path))
    if scenario == "hitl":
        request_id = str(manifest.get("request_id") or "")
        respond_to_human_input(
            HumanInputResponseCommand(
                human_input_root=str(root / "human_input"),
                request_id=request_id,
                answer=answer,
            )
        )
    else:
        operation_key = str(manifest.get("operation_key") or "")
        decide_approval(
            str(root / "approvals"),
            operation_key,
            "approved",
            note="approved during deterministic control-plane showcase",
        )

    metadata = checkpoint.metadata if isinstance(checkpoint.metadata, dict) else {}
    result = _run_phase(
        scenario,
        run_dir=root,
        workspace=Path(str(manifest["workspace"])),
        resume_state=checkpoint_path,
        human_thread_id=str(metadata.get("human_thread_id") or checkpoint.run_id),
    )
    result = replace(
        result,
        request_id=str(manifest.get("request_id") or ""),
        operation_key=str(manifest.get("operation_key") or ""),
    )
    _write_showcase_artifacts(result)
    return result


def _run_phase(
    scenario: str,
    *,
    run_dir: Path,
    workspace: Path,
    resume_state: Path | None = None,
    human_thread_id: str = "",
) -> ControlPlaneShowcaseResult:
    """经唯一 ``Harness`` Public API 装配 deterministic control-plane phase。"""

    model = _HitlShowcaseModel() if scenario == "hitl" else _ApprovalShowcaseModel()
    task = HITL_TASK if scenario == "hitl" else APPROVAL_TASK
    tools = build_registry(
        ToolRegistryBuildRequest(
            workspace=str(workspace),
            auto=True,
        )
    )
    result = Harness(
        model=model,
        tools=tools,
        config=HarnessConfig(
            workspace=str(workspace),
            output_root=str(run_dir / "phases"),
            max_steps=3,
            approval_root=str(run_dir / "approvals"),
            human_input_root=str(run_dir / "human_input"),
            operation_ledger_root=str(run_dir / "operation_ledger"),
            memory_root=str(run_dir / "memory"),
            auto_approve_writes=scenario != "approval",
            approval_mode="trusted",
            tool_routing_mode="task-aware",
        ),
    ).run(
        RunRequest(
            task=task,
            workspace=str(workspace),
            resume_state=str(resume_state or ""),
            human_thread_id=human_thread_id,
            agent_name="ShowcaseAgent",
        )
    )
    if result.trace_path is None:
        raise RuntimeError("governed demo requires the default trace adapter")

    checkpoint_path = result.artifact_dir / "task_state" / f"{result.run_id}.json"
    checkpoint = result.checkpoint
    request_id = ""
    operation_key = ""
    next_command = ""
    if checkpoint.status == "waiting_human":
        pending_inputs = list_pending_human_inputs(str(run_dir / "human_input"))
        request_id = pending_inputs[0].request_id
        next_command = (
            "forge showcase hitl continue "
            f"{shlex.quote(str(run_dir))} --answer 'Python 3.11'"
        )
    elif checkpoint.status == "waiting_approval":
        pending_approvals = list_pending_approvals(str(run_dir / "approvals"))
        operation_key = pending_approvals[0].operation_key
        next_command = f"forge showcase approval continue {shlex.quote(str(run_dir))}"

    return ControlPlaneShowcaseResult(
        scenario=scenario,
        status=checkpoint.status,
        run_dir=run_dir,
        artifact_dir=result.artifact_dir,
        workspace=workspace,
        checkpoint_path=checkpoint_path,
        trace_path=result.trace_path,
        request_id=request_id,
        operation_key=operation_key,
        next_command=next_command,
    )

def _new_run_dir(output_root: str | Path, scenario: str) -> Path:
    run_id = f"{scenario}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    run_dir = Path(output_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _validate_scenario(scenario: str) -> None:
    if scenario not in {"hitl", "approval"}:
        raise ValueError(f"unsupported control-plane showcase: {scenario}")


def _write_showcase_artifacts(result: ControlPlaneShowcaseResult) -> None:
    """同时提交机器可读 manifest 与现场可读报告。"""

    payload = {
        "scenario": result.scenario,
        "status": result.status,
        "run_dir": str(result.run_dir),
        "artifact_dir": str(result.artifact_dir),
        "workspace": str(result.workspace),
        "checkpoint_path": str(result.checkpoint_path),
        "trace_path": str(result.trace_path),
        "request_id": result.request_id,
        "operation_key": result.operation_key,
        "next_command": result.next_command,
    }
    (result.run_dir / "showcase.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (result.run_dir / "showcase.md").write_text(
        _render_showcase_report(result),
        encoding="utf-8",
    )


def _render_showcase_report(result: ControlPlaneShowcaseResult) -> str:
    """把本次真实状态压缩成单页现场演示证据。"""

    is_waiting = result.status.startswith("waiting_")
    if result.scenario == "hitl":
        event = (
            "Agent 提出问题，request 已持久化，run 停在 waiting_human。"
            if is_waiting
            else "人工回答已持久化，新 run 加载 checkpoint 与回答后完成。"
        )
        safety = "ask_human 是同 turn barrier；等待期间不会执行该响应中的其他工具。"
        identity = f"human request: `{result.request_id}`"
    else:
        target = result.workspace / "target.py"
        current_value = target.read_text(encoding="utf-8").strip()
        event = (
            "写操作已登记并等待审批，真实工具尚未执行。"
            if is_waiting
            else "审批已绑定到原 operation fingerprint，补丁随后由真实工具执行。"
        )
        safety = f"当前 `target.py` 内容：`{current_value}`。"
        identity = f"approval operation: `{result.operation_key}`"

    lines = [
        "# Runtime Control Plane Showcase",
        "",
        f"- scenario: `{result.scenario}`",
        f"- current state: **{result.status}**",
        f"- {identity}",
        "- model boundary: deterministic tool-call stimulus",
        "- runtime boundary: production AgentLoop and repositories",
        "",
        "## 本步发生了什么",
        "",
        event,
        "",
        "## 安全断言",
        "",
        safety,
        "",
        "## 本次运行证据",
        "",
        f"- checkpoint: `{result.checkpoint_path}`",
        f"- trace: `{result.trace_path}`",
        f"- canonical artifacts: `{result.artifact_dir}`",
        f"- workspace: `{result.workspace}`",
    ]
    if result.next_command:
        lines.extend(
            [
                "",
                "## 下一步",
                "",
                "```bash",
                result.next_command,
                "```",
            ]
        )
    else:
        lines.extend(["", "## 结果", "", "控制面 continuation 已完成。"])
    return "\n".join(lines) + "\n"


def _render_governed_demo(
    waiting: ControlPlaneShowcaseResult,
    completed: ControlPlaneShowcaseResult,
) -> str:
    lines = [
        "# Governed Run Demo",
        "",
        "本演示使用确定性 ModelPort 固定工具意图，但 checkpoint、审批/HITL、",
        "operation ledger、工具执行和 continuation 均经过正式 Runtime。它证明控制面，",
        "不证明在线模型能力或 official resolved。",
        "",
        f"- scenario: `{waiting.scenario}`",
        f"- waiting state: `{waiting.status}`",
        f"- completed state: `{completed.status}`",
        f"- checkpoint: `{waiting.checkpoint_path}`",
        f"- start trace: `{waiting.trace_path}`",
        f"- continuation trace: `{completed.trace_path}`",
        f"- canonical Run Story: `{completed.artifact_dir / 'run_manifest.json'}`",
        "",
        "## 状态序列",
        "",
        f"`running → {waiting.status} → explicit human decision → {completed.status}`",
        "",
        "## Claim Boundary",
        "",
        "- proves: 人工屏障先持久化、continuation 显式加载 checkpoint、写副作用受治理。",
        "- does not prove: 模型任务质量、测试通过、SWE-bench official resolved。",
        "",
    ]
    return "\n".join(lines)


def _load_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "showcase.json"
    if not path.exists():
        raise ValueError(f"showcase manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "ControlPlaneShowcaseResult",
    "GovernedRunDemoResult",
    "continue_control_plane_showcase",
    "run_governed_demo",
    "start_control_plane_showcase",
]
