"""HITL 与状态变更操作审批的可重复控制面展示。

这里仅用确定性 ``ModelPort`` 固定模型会提出什么工具请求；暂停、持久化、审批、
checkpoint、恢复和文件修改全部经过正式 Runtime。这样现场演示不依赖模型随机性，
也不会把测试替身误说成完整能力。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from agent_forge.control import RunController
from agent_forge.contracts import ToolSchema
from agent_forge.harness import (
    Harness,
    HarnessConfig,
    HarnessExtensions,
    RunRequest,
)
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
from agent_forge.runtime.ports import ModelPort
from agent_forge.runtime.wiring import build_registry
from agent_forge.storage_layout import (
    INDEX_ROOT,
    SHOWCASE_RUN_ROOT,
    human_readable_run_name,
)

HITL_QUESTION = "What operator input should the continuation use?"
HITL_TASK = (
    "Ask the operator for the missing input, persist it, and finish only after the "
    "continuation loads the response."
)
APPROVAL_TASK = (
    "Fix target.py so value equals 2, then run the focused pytest target "
    "test_target.py before finishing."
)
DEFAULT_GOVERNED_REQUEST = "Record this operator-approved change request."
GOVERNED_PLACEHOLDER = "NO_OPERATOR_REQUEST"
GOVERNED_TASK = (
    "Ask the operator for a concrete change request, write it to operator_request.txt "
    "only after explicit approval, and run the focused pytest target "
    "test_operator_request.py before finishing."
)


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
    action: str = "runtime_checkpoint"
    durable_paths: tuple[Path, ...] = ()
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernedRunDemoResult:
    """一个命令完成的等待→人工决定→continuation 演示摘要。"""

    scenario: str
    run_dir: Path
    waiting_status: str
    completed_status: str
    report_path: Path
    inspect_target: Path
    state_sequence: tuple[str, ...] = ()


class GovernedShowcaseController:
    """按钮式展示层使用的受治理场景状态机。

    Controller 只编排公开的人类动作；每个 phase 仍通过 ``Harness`` 创建新的
    continuation，并从 durable checkpoint、HumanInput/Approval Repository 恢复。
    """

    def __init__(
        self,
        *,
        output_root: str | Path = SHOWCASE_RUN_ROOT,
    ) -> None:
        self.output_root = Path(output_root)
        self._phases: list[ControlPlaneShowcaseResult] = []
        self._current: ControlPlaneShowcaseResult | None = None
        self._pending_resume = ""

    @property
    def current(self) -> ControlPlaneShowcaseResult | None:
        return self._current

    @property
    def state_sequence(self) -> tuple[str, ...]:
        return tuple(phase.status for phase in self._phases)

    def start(self) -> ControlPlaneShowcaseResult:
        if self._phases:
            raise RuntimeError("governed showcase has already started")
        result = _start_control_plane_demo(
            "governed",
            output_root=self.output_root,
        )
        self._phases.append(result)
        self._current = result
        return result

    def record_answer(self, answer: str) -> ControlPlaneShowcaseResult:
        """只持久化 HumanInput 回答；明确等待下一次 ``resume``。"""

        current = self._require_status("waiting_human")
        if self._pending_resume:
            raise RuntimeError(
                "governed showcase already has a persisted operator action"
            )
        normalized_answer = str(answer or "").strip()
        if not normalized_answer:
            raise ValueError("governed showcase answer must not be empty")
        result = _record_human_answer(current, normalized_answer)
        self._pending_resume = "human_input"
        self._current = result
        return result

    def answer(self, answer: str) -> ControlPlaneShowcaseResult:
        """兼容一键调用方；TUI 使用 ``record_answer`` 与 ``resume`` 两步。"""

        self.record_answer(answer)
        return self.resume()

    def record_decision(
        self,
        decision: Literal["approved", "rejected"],
    ) -> ControlPlaneShowcaseResult:
        """只持久化 Approval 决定；不执行真实写工具。"""

        current = self._require_status("waiting_approval")
        if self._pending_resume:
            raise RuntimeError(
                "governed showcase already has a persisted operator action"
            )
        result = _record_approval_decision(current, decision)
        self._pending_resume = "approval"
        self._current = result
        return result

    def resume(self) -> ControlPlaneShowcaseResult:
        """显式从当前 durable checkpoint 创建 continuation。"""

        current = self.current
        if current is None:
            raise RuntimeError("governed showcase has not started")
        if current.status == "paused" and not self._pending_resume:
            self._pending_resume = "paused"
        if not self._pending_resume:
            raise RuntimeError("governed showcase has no persisted action to resume")
        result = _resume_control_plane_demo(
            "governed",
            current.run_dir,
        )
        self._phases.append(result)
        self._current = result
        self._pending_resume = ""
        if not result.status.startswith("waiting_") and result.status != "paused":
            self._publish_terminal_story(result)
        return result

    def pause_at_safe_boundary(self) -> ControlPlaneShowcaseResult:
        """让下一次 continuation 经正式 RunControlPort 落为 paused checkpoint。"""

        current = self.current
        if current is None or not self._pending_resume:
            raise RuntimeError("pause requires a persisted action waiting to resume")
        result = _resume_control_plane_demo(
            "governed",
            current.run_dir,
            control_action="pause",
        )
        self._phases.append(result)
        self._current = result
        return result

    def cancel(self) -> ControlPlaneShowcaseResult:
        """让 continuation 在下一安全边界取消，并保留此前全部持久化事实。"""

        current = self.current
        if current is None:
            raise RuntimeError("governed showcase has not started")
        result = _resume_control_plane_demo(
            "governed",
            current.run_dir,
            control_action="cancel",
        )
        self._phases.append(result)
        self._current = result
        self._pending_resume = ""
        self._publish_terminal_story(result)
        return result

    def decide(
        self,
        decision: Literal["approved", "rejected"],
    ) -> ControlPlaneShowcaseResult:
        self.record_decision(decision)
        return self.resume()

    def _require_status(self, expected: str) -> ControlPlaneShowcaseResult:
        current = self.current
        if current is None or current.status != expected:
            actual = current.status if current is not None else "not_started"
            raise RuntimeError(
                f"governed showcase requires {expected}, current status is {actual}"
            )
        return current

    def _publish_terminal_story(self, result: ControlPlaneShowcaseResult) -> None:
        report_path = result.run_dir / "demo.md"
        report_path.write_text(
            _render_governed_demo(self._phases),
            encoding="utf-8",
        )
        _publish_default_demo_pointer(self.output_root, result.artifact_dir)


# 主要入口：用一个命令展示真实 Runtime 的人工屏障和 continuation。
def run_governed_demo(
    scenario: str = "governed",
    *,
    output_root: str | Path = SHOWCASE_RUN_ROOT,
    answer: str = "",
) -> GovernedRunDemoResult:
    """串联全部正式 Runtime phase；确定性模型只固定工具意图。"""

    phases = [_start_control_plane_demo(scenario, output_root=output_root)]
    if phases[-1].status == "waiting_human":
        resolved_answer = answer or DEFAULT_GOVERNED_REQUEST
        phases.append(
            _continue_control_plane_demo(
                scenario,
                phases[-1].run_dir,
                answer=resolved_answer,
            )
        )
    if phases[-1].status == "waiting_approval":
        phases.append(
            _continue_control_plane_demo(
                scenario,
                phases[-1].run_dir,
                decision="approved",
            )
        )
    waiting = phases[0]
    completed = phases[-1]
    report_path = waiting.run_dir / "demo.md"
    report_path.write_text(
        _render_governed_demo(phases),
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
        state_sequence=tuple(phase.status for phase in phases),
    )


def _publish_default_demo_pointer(output_root: str | Path, artifact_dir: Path) -> None:
    """让默认 CLI demo 可立即用 ``forge inspect latest`` 查看。"""

    if Path(output_root) != SHOWCASE_RUN_ROOT:
        return
    latest = INDEX_ROOT
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
                            "choices": [],
                        },
                    )
                ],
            )
        return AgentResponse(
            "PASS\noperator response loaded; continuation completed", []
        )


class _ApprovalShowcaseModel:
    """固定提出文件补丁和 focused pytest，控制面与工具仍走正式 Runtime。"""

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
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    )
                ],
            )
        if self.calls == 2:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "showcase-focused-pytest",
                        "python_validation",
                        {
                            "check_type": "pytest",
                            "validation_target": "test_target.py",
                        },
                    )
                ],
            )
        return AgentResponse(
            "PASS\napproved patch executed; focused pytest passed; continuation completed",
            [],
        )


class _GovernedShowcaseModel:
    """固定“人工输入 → 审批写入 → focused pytest”的工具意图。

    continuation 会创建新的 ModelPort，因此阶段判断只读取当前规范消息中的已回填
    Tool Observation，不依赖进程内计数或隐藏状态。
    """

    last_usage = None

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        tool_messages = [message for message in messages if message.role == "tool"]
        tool_names = {str(message.name or "") for message in tool_messages}
        if "python_validation" in tool_names:
            return AgentResponse(
                "PASS\noperator request approved; request artifact updated; "
                "focused pytest passed",
                [],
            )
        if "replace_text" in tool_names:
            replace_observation = next(
                (
                    str(message.content or "")
                    for message in reversed(tool_messages)
                    if message.name == "replace_text"
                ),
                "",
            )
            if "approval rejected" in replace_observation:
                return AgentResponse(
                    "STOPPED\noperator rejected the proposed request update; "
                    "workspace remained unchanged",
                    [],
                )
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "showcase-governed-pytest",
                        "python_validation",
                        {
                            "check_type": "pytest",
                            "validation_target": "test_operator_request.py",
                        },
                    )
                ],
            )
        operator_request = _human_response(tool_messages)
        if operator_request:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "showcase-governed-request-update",
                        "replace_text",
                        {
                            "path": "operator_request.txt",
                            "old": f"{GOVERNED_PLACEHOLDER}\n",
                            "new": f"{operator_request}\n",
                        },
                    )
                ],
            )
        return AgentResponse(
            None,
            [
                ToolCall(
                    "showcase-governed-input",
                    "ask_human",
                    {
                        "question": "Describe the change request to persist and approve.",
                        "choices": [],
                    },
                )
            ],
        )


def _human_response(messages: list[Message]) -> str:
    for message in reversed(messages):
        content = str(message.content or "")
        if message.name == "ask_human" and content.startswith("human_response:"):
            return content.partition(":")[2].strip()
    return ""


def _start_control_plane_demo(
    scenario: str,
    *,
    output_root: str | Path = SHOWCASE_RUN_ROOT,
) -> ControlPlaneShowcaseResult:
    """启动展示并在人工控制点返回，不自动回答或批准。"""

    _validate_scenario(scenario)
    run_dir = _new_run_dir(output_root, scenario)
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    if scenario == "approval":
        (workspace / "target.py").write_text("value = 1\n", encoding="utf-8")
        (workspace / "test_target.py").write_text(
            "from target import value\n\n\ndef test_value() -> None:\n"
            "    assert value == 2\n",
            encoding="utf-8",
        )
    elif scenario == "governed":
        (workspace / "operator_request.txt").write_text(
            f"{GOVERNED_PLACEHOLDER}\n",
            encoding="utf-8",
        )
        (workspace / "test_operator_request.py").write_text(
            "from pathlib import Path\n\n\n"
            "def test_operator_request_is_recorded() -> None:\n"
            "    request = Path(__file__).with_name('operator_request.txt')\n"
            "    value = request.read_text(encoding='utf-8').strip()\n"
            "    assert value\n"
            f"    assert value != {GOVERNED_PLACEHOLDER!r}\n",
            encoding="utf-8",
        )

    result = _run_phase(
        scenario,
        run_dir=run_dir,
        workspace=workspace,
    )
    _write_showcase_artifacts(result)
    return result


def _continue_control_plane_demo(
    scenario: str,
    run_dir: str | Path,
    *,
    answer: str = "",
    decision: Literal["approved", "rejected"] = "approved",
) -> ControlPlaneShowcaseResult:
    """兼容 headless 演示：保存决定后立即进入 continuation。"""

    _validate_scenario(scenario)
    root = Path(run_dir).resolve()
    manifest = _load_manifest(root)
    expected = manifest.get("scenario")
    if expected != scenario:
        raise ValueError(
            f"showcase scenario mismatch: expected {expected!r}, got {scenario!r}"
        )

    current = _result_from_manifest(root, manifest)
    checkpoint = load_task_checkpoint(str(current.checkpoint_path))
    if scenario in {"hitl", "governed"} and checkpoint.status == "waiting_human":
        _record_human_answer(current, answer)
    elif checkpoint.status == "waiting_approval":
        _record_approval_decision(current, decision)
    else:
        raise RuntimeError(
            "control-plane continuation requires waiting_human or waiting_approval, "
            f"got {checkpoint.status}"
        )
    return _resume_control_plane_demo(scenario, root)


def _record_human_answer(
    current: ControlPlaneShowcaseResult,
    answer: str,
) -> ControlPlaneShowcaseResult:
    """经正式 HumanInput API 原子保存回答，但不自动恢复 AgentLoop。"""

    respond_to_human_input(
        HumanInputResponseCommand(
            human_input_root=str(current.run_dir / "human_input"),
            request_id=current.request_id,
            answer=answer,
        )
    )
    result = replace(
        current,
        action="human_input_recorded",
        durable_paths=(
            current.run_dir / "human_input" / f"{current.request_id}.json",
            current.checkpoint_path,
            current.run_dir / "showcase.json",
        ),
        changed_fields=("status", "answer", "updated_at"),
    )
    _write_showcase_artifacts(result)
    return result


def _record_approval_decision(
    current: ControlPlaneShowcaseResult,
    decision: Literal["approved", "rejected"],
) -> ControlPlaneShowcaseResult:
    """经正式 Approval API 保存决定，但不执行对应状态变更操作。"""

    decide_approval(
        str(current.run_dir / "approvals"),
        current.operation_key,
        decision,
        note=f"{decision} during deterministic control-plane showcase",
    )
    result = replace(
        current,
        action="approval_recorded",
        durable_paths=(
            current.run_dir / "approvals" / f"{current.operation_key}.json",
            current.run_dir / "operation_ledger" / f"{current.operation_key}.json",
            current.checkpoint_path,
            current.run_dir / "showcase.json",
        ),
        changed_fields=("status", "decision_note", "updated_at"),
    )
    _write_showcase_artifacts(result)
    return result


def _resume_control_plane_demo(
    scenario: str,
    run_dir: str | Path,
    *,
    control_action: Literal["", "pause", "cancel"] = "",
) -> ControlPlaneShowcaseResult:
    """只负责 checkpoint continuation；HumanInput/Approval 必须已单独持久化。"""

    _validate_scenario(scenario)
    root = Path(run_dir).resolve()
    manifest = _load_manifest(root)
    if manifest.get("scenario") != scenario:
        raise ValueError(
            "showcase scenario mismatch: "
            f"expected {manifest.get('scenario')!r}, got {scenario!r}"
        )
    checkpoint_path = Path(str(manifest["checkpoint_path"]))
    checkpoint = load_task_checkpoint(str(checkpoint_path))

    metadata = checkpoint.metadata if isinstance(checkpoint.metadata, dict) else {}
    result = _run_phase(
        scenario,
        run_dir=root,
        workspace=Path(str(manifest["workspace"])),
        resume_state=checkpoint_path,
        human_thread_id=str(metadata.get("human_thread_id") or checkpoint.run_id),
        control_action=control_action,
    )
    result = replace(
        result,
        request_id=str(result.request_id or manifest.get("request_id") or ""),
        operation_key=str(result.operation_key or manifest.get("operation_key") or ""),
    )
    _write_showcase_artifacts(result)
    return result


def _result_from_manifest(
    run_dir: Path,
    manifest: dict[str, Any],
) -> ControlPlaneShowcaseResult:
    """把 run-level 导航清单还原为控制器当前结果，不复制权威状态。"""

    return ControlPlaneShowcaseResult(
        scenario=str(manifest.get("scenario") or ""),
        status=str(manifest.get("status") or ""),
        run_dir=run_dir,
        artifact_dir=Path(str(manifest["artifact_dir"])),
        workspace=Path(str(manifest["workspace"])),
        checkpoint_path=Path(str(manifest["checkpoint_path"])),
        trace_path=Path(str(manifest["trace_path"])),
        request_id=str(manifest.get("request_id") or ""),
        operation_key=str(manifest.get("operation_key") or ""),
        action=str(manifest.get("action") or "runtime_checkpoint"),
        durable_paths=tuple(
            Path(str(path)) for path in manifest.get("durable_paths") or []
        ),
        changed_fields=tuple(
            str(field) for field in manifest.get("changed_fields") or []
        ),
    )


def _run_phase(
    scenario: str,
    *,
    run_dir: Path,
    workspace: Path,
    resume_state: Path | None = None,
    human_thread_id: str = "",
    control_action: Literal["", "pause", "cancel"] = "",
) -> ControlPlaneShowcaseResult:
    """经唯一 ``Harness`` Public API 装配 deterministic control-plane phase。"""

    model: ModelPort
    if scenario == "hitl":
        model = _HitlShowcaseModel()
    elif scenario == "approval":
        model = _ApprovalShowcaseModel()
    else:
        model = _GovernedShowcaseModel()
    task = {
        "hitl": HITL_TASK,
        "approval": APPROVAL_TASK,
        "governed": GOVERNED_TASK,
    }[scenario]
    tools = build_registry(
        ToolRegistryBuildRequest(
            workspace=str(workspace),
            auto=True,
            memory_root=str(run_dir / "memory"),
            memory_namespace=str(workspace.resolve()),
        )
    )
    run_controller = RunController()
    if control_action == "pause":
        run_controller.pause("Lab 1 operator requested pause before continuation")
    elif control_action == "cancel":
        run_controller.cancel("Lab 1 operator requested cancel before continuation")
    phase_label = "lab1-governed-change" if scenario == "governed" else f"lab1-{scenario}"
    run_result = Harness(
        model=model,
        tools=tools,
        config=HarnessConfig(
            workspace=str(workspace),
            output_root=str(run_dir / "phases"),
            max_steps=5 if scenario == "governed" else 3,
            approval_root=str(run_dir / "approvals"),
            human_input_root=str(run_dir / "human_input"),
            operation_ledger_root=str(run_dir / "operation_ledger"),
            memory_root=str(run_dir / "memory"),
            auto_approve_writes=scenario not in {"approval", "governed"},
            approval_mode="trusted",
            tool_routing_mode="task-aware",
        ),
        extensions=HarnessExtensions(run_control=run_controller),
    ).run(
        RunRequest(
            task=task,
            workspace=str(workspace),
            resume_state=str(resume_state or ""),
            human_thread_id=human_thread_id,
            agent_name="ShowcaseAgent",
            run_label=f"{phase_label}-{'continuation' if resume_state else 'start'}",
        )
    )
    if run_result.trace_path is None:
        raise RuntimeError("governed demo requires the default trace adapter")

    checkpoint_path = (
        run_result.artifact_dir / "task_state" / f"{run_result.run_id}.json"
    )
    checkpoint = run_result.checkpoint
    request_id = ""
    operation_key = ""
    if checkpoint.status == "waiting_human":
        pending_inputs = list_pending_human_inputs(str(run_dir / "human_input"))
        request_id = pending_inputs[0].request_id
    elif checkpoint.status == "waiting_approval":
        pending_approvals = list_pending_approvals(str(run_dir / "approvals"))
        operation_key = pending_approvals[0].operation_key

    result = ControlPlaneShowcaseResult(
        scenario=scenario,
        status=checkpoint.status,
        run_dir=run_dir,
        artifact_dir=run_result.artifact_dir,
        workspace=workspace,
        checkpoint_path=checkpoint_path,
        trace_path=run_result.trace_path,
        request_id=request_id,
        operation_key=operation_key,
        action=(
            "run_paused"
            if checkpoint.status == "paused"
            else "run_cancelled"
            if checkpoint.status == "cancelled"
            else "runtime_checkpoint"
        ),
    )
    return replace(result, durable_paths=_durable_paths(result))


def _new_run_dir(output_root: str | Path, scenario: str) -> Path:
    labels = {
        "governed": "lab1-governed-change-control",
        "hitl": "lab1-human-input",
        "approval": "lab1-write-operation-approval",
    }
    run_id = human_readable_run_name(labels.get(scenario, scenario))
    run_dir = Path(output_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _validate_scenario(scenario: str) -> None:
    if scenario not in {"hitl", "approval", "governed"}:
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
        "action": result.action,
        "durable_paths": [str(path) for path in result.durable_paths],
        "changed_fields": list(result.changed_fields),
    }
    (result.run_dir / "showcase.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (result.run_dir / "showcase.md").write_text(
        _render_showcase_report(result),
        encoding="utf-8",
    )


def _durable_paths(result: ControlPlaneShowcaseResult) -> tuple[Path, ...]:
    """列出本步真正写入的权威文件；仅用于导航，不创建展示副本。"""

    paths = [result.checkpoint_path]
    if result.request_id:
        paths.append(result.run_dir / "human_input" / f"{result.request_id}.json")
    if result.operation_key:
        paths.extend(
            [
                result.run_dir / "approvals" / f"{result.operation_key}.json",
                result.run_dir / "operation_ledger" / f"{result.operation_key}.json",
            ]
        )
    paths.extend([result.trace_path, result.run_dir / "showcase.json"])
    return tuple(dict.fromkeys(paths))


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
    elif result.scenario == "approval":
        target = result.workspace / "target.py"
        current_value = target.read_text(encoding="utf-8").strip()
        event = (
            "写操作已登记并等待审批，真实工具尚未执行。"
            if is_waiting
            else (
                "审批已绑定到原 operation fingerprint，补丁由真实工具执行，"
                "随后 focused pytest 形成独立验证证据。"
            )
        )
        safety = f"当前 `target.py` 内容：`{current_value}`。"
        identity = f"approval operation: `{result.operation_key}`"
    else:
        target = result.workspace / "operator_request.txt"
        current_request = target.read_text(encoding="utf-8").strip()
        if result.status == "waiting_human":
            event = "人工输入问题已持久化；在操作员回答前不会产生补丁。"
            safety = f"当前请求文件仍为 `{current_request}`。"
            identity = f"human request: `{result.request_id}`"
        elif result.status == "waiting_approval":
            event = (
                "人工输入已回填，Runtime 已生成绑定目标 fingerprint 的补丁审批；"
                "真实写工具尚未执行。"
            )
            safety = f"审批前请求文件仍为 `{current_request}`。"
            identity = f"approval operation: `{result.operation_key}`"
        else:
            event = (
                "审批决定已持久化；获准时补丁由真实工具执行，并由 focused pytest "
                "验证人工请求已写入。"
            )
            safety = f"终态请求文件为 `{current_request}`。"
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
    result_text = (
        "控制面已停在人工屏障；公开 `forge demo` 会在同一演示中提交决定并继续。"
        if is_waiting
        else "控制面 continuation 已完成。"
    )
    lines.extend(["", "## 结果", "", result_text])
    return "\n".join(lines) + "\n"


def _render_governed_demo(
    phases: list[ControlPlaneShowcaseResult],
) -> str:
    if not phases:
        raise ValueError("governed demo requires at least one phase")
    waiting = phases[0]
    completed = phases[-1]
    state_sequence = " → ".join(phase.status for phase in phases)
    if waiting.scenario == "governed":
        decision_steps = [
            "模型提出人工问题，Runtime 先保存 request 与 waiting_human checkpoint。",
            "操作员输入变更要求，continuation 再生成具体文件补丁。",
            "Runtime 保存 operation fingerprint，并停在 waiting_approval。",
            "操作员批准或拒绝；只有批准分支才执行写工具与 focused pytest。",
        ]
    elif waiting.scenario == "hitl":
        decision_steps = [
            "模型提出人工问题，Runtime 保存 request 与 waiting_human checkpoint。",
            "操作员回答后，continuation 从持久状态恢复并完成任务。",
        ]
    else:
        decision_steps = [
            "模型提出写操作，Runtime 保存 operation fingerprint 并等待审批。",
            "操作员批准后，continuation 执行写工具与 focused pytest。",
        ]
    lines = [
        "# Governed Run Demo",
        "",
        "本演示使用确定性 ModelPort 固定工具意图，但 checkpoint、审批/HITL、",
        "操作状态表、工具执行和 continuation 均经过正式 Runtime。它证明控制面，",
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
        f"`running → {state_sequence}`",
        "",
        "## 人工决策链",
        "",
        *(f"{index}. {step}" for index, step in enumerate(decision_steps, start=1)),
        "",
        "## Claim Boundary",
        "",
        "- proves: 人工屏障先持久化、continuation 显式加载 checkpoint、"
        "写操作受治理且 focused pytest 形成验证证据。",
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
    "DEFAULT_GOVERNED_REQUEST",
    "GovernedRunDemoResult",
    "GovernedShowcaseController",
    "run_governed_demo",
]
