"""Lab 1 的按钮式 Runtime 控制台。

界面只调用 ``GovernedShowcaseController``；人工输入、审批、Checkpoint、工具执行
和验证均由正式 Runtime 完成。Textual 不直接改 workspace 或控制仓储。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from apps.showcase.control_plane import (
    GOVERNED_PLACEHOLDER,
    ControlPlaneShowcaseResult,
    GovernedShowcaseController,
)

OpenWorkbench = Callable[[ControlPlaneShowcaseResult], None]


def _readable_durable_paths(result: ControlPlaneShowcaseResult) -> str:
    """用稳定对象名代替随机 ID；精确路径仍在 manifest 中。"""

    rendered: list[str] = []
    for path in result.durable_paths:
        if path == result.checkpoint_path:
            display = "检查点 → task_state/<run-id>.json"
        elif path == result.trace_path:
            display = "Trace → current_phase/trace.json"
        else:
            try:
                relative = path.resolve().relative_to(result.run_dir.resolve())
            except ValueError:
                display = path.name
            else:
                labels = {
                    "human_input": "人工输入 → human_input/<request-id>.json",
                    "approvals": "审批 → approvals/<operation-key>.json",
                    "operation_ledger": "Ledger → operation_ledger/<key>.json",
                    "showcase.json": "导航 → showcase.json",
                }
                display = labels.get(relative.parts[0], relative.as_posix())
        rendered.append(f"• {display}")
    return "\n".join(rendered)


class GovernedShowcaseConsoleApp(App[None]):
    """把两个 durable 人工屏障投影成可点击的一屏控制台。"""

    TITLE = "NanoHarness · Governed Repair"
    SUB_TITLE = "Human input · Approval · Checkpoint · Validation · Workbench"
    CSS = """
    Screen { background: #101417; color: #e8edf0; }
    Header, Footer { background: #182025; }
    #hero { height: 5; padding: 1 2; border-bottom: solid #3d4a52; }
    #hero-title { text-style: bold; color: #b6e3c4; }
    #hero-copy { color: #bcc7cc; }
    #body { height: 1fr; }
    #timeline-panel { width: 3fr; padding: 1 2; border-right: solid #3d4a52; }
    #control-panel { width: 2fr; min-width: 48; padding: 1 2; }
    .panel-title { height: 2; text-style: bold; color: #b6e3c4; }
    #timeline { height: 1fr; background: #101417; }
    #state { height: 5; padding: 0 1; border: solid #4c5a62; background: #171d21; }
    #persistence { height: 8; margin-top: 1; padding: 0 1; border: solid #416b55; }
    #decision { height: 1fr; margin-top: 1; padding: 0 1; border: solid #4c5a62; }
    #actions { dock: bottom; height: 4; margin-top: 1; background: #101417; }
    #human-input-actions, #approval-actions, #resume-actions, #terminal-actions { height: 3; }
    #human-answer { width: 1fr; }
    #save-human-answer { width: auto; min-width: 14; }
    Button { margin-right: 1; }
    #approve { background: #247a4a; }
    #reject { background: #8b3a3a; }
    #cancel { background: #8b3a3a; }
    #resume { background: #247a4a; }
    #workbench { background: #285f88; }
    """
    BINDINGS = [("ctrl+q", "quit", "退出")]

    def __init__(
        self,
        *,
        output_root: str | Path,
        open_workbench: OpenWorkbench | None = None,
        controller: GovernedShowcaseController | None = None,
    ) -> None:
        super().__init__()
        self._output_root = Path(output_root)
        self._open_workbench = open_workbench
        self._controller = controller or GovernedShowcaseController(
            output_root=self._output_root
        )
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="hero"):
            yield Static("受治理人工变更", id="hero-title")
            yield Static(
                "同一任务依次经过人工输入、补丁审批和 focused pytest。"
                "每一步都先落 durable evidence，再允许 continuation。",
                id="hero-copy",
            )
        with Horizontal(id="body"):
            with Vertical(id="timeline-panel"):
                yield Static("状态与证据时间线", classes="panel-title")
                yield RichLog(id="timeline", wrap=True, markup=False)
            with Vertical(id="control-panel"):
                yield Static("Runtime Control", classes="panel-title")
                yield Static(id="state")
                yield Static(id="persistence")
                yield Static(id="decision")
                with Vertical(id="actions"):
                    yield Button("开始受治理任务", id="start", variant="primary")
                    with Horizontal(id="human-input-actions"):
                        yield Input(
                            placeholder="输入一条变更要求",
                            id="human-answer",
                        )
                        yield Button("仅保存输入", id="save-human-answer")
                    with Horizontal(id="approval-actions"):
                        yield Button("仅保存批准", id="approve")
                        yield Button("仅保存拒绝", id="reject")
                    with Horizontal(id="resume-actions"):
                        yield Button("显式 Resume", id="resume")
                        yield Button("Pause 到安全边界", id="pause")
                        yield Button("Cancel 任务", id="cancel")
                    with Horizontal(id="terminal-actions"):
                        yield Button("打开 Evidence Workbench", id="workbench")
                        yield Button("重新开始", id="restart")
        yield Footer()

    def on_mount(self) -> None:
        self._reset_ready()

    def _reset_ready(self) -> None:
        self._show_actions("ready")
        self.query_one("#human-answer", Input).value = ""
        self.query_one("#state", Static).update(
            "状态：READY\n等待点击开始；workspace 尚未创建。"
        )
        self.query_one("#decision", Static).update(
            "操作者决定不会被自动代填。\n"
            "按钮只调用 HumanInput / Approval 控制端口，"
            "不在 UI 内直接改文件。"
        )
        self.query_one("#persistence", Static).update(
            "本步持久化：尚未创建 Run。\n"
            "开始后显示权威对象；showcase.json 保留精确路径。"
        )
        self._timeline("READY · 尚未运行模型或工具")

    @on(Button.Pressed, "#start")
    def start(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "正在启动 Runtime，等待第一个 durable 人工屏障……")
        self._execute_start()

    @on(Button.Pressed, "#save-human-answer")
    def save_human_answer(self) -> None:
        self._answer(self.query_one("#human-answer", Input).value)

    @on(Input.Submitted, "#human-answer")
    def submit_human_answer(self, event: Input.Submitted) -> None:
        self._answer(event.value)

    def _answer(self, answer: str) -> None:
        if self._busy:
            return
        normalized_answer = str(answer or "").strip()
        if not normalized_answer:
            self.query_one("#decision", Static).update(
                "请先输入一条非空变更要求；尚未写入任何状态。"
            )
            self.query_one("#human-answer", Input).focus()
            return
        self._set_busy(True, "正在持久化人工输入；本步不会自动 Resume……")
        self._execute_answer(normalized_answer)

    @on(Button.Pressed, "#approve")
    def approve(self) -> None:
        self._decide("approved")

    @on(Button.Pressed, "#reject")
    def reject(self) -> None:
        self._decide("rejected")

    def _decide(self, decision: Literal["approved", "rejected"]) -> None:
        if self._busy:
            return
        message = "正在保存批准决定；本步不会执行补丁……"
        if decision == "rejected":
            message = "正在保存拒绝决定；本步不会自动结束任务……"
        self._set_busy(True, message)
        self._execute_decision(decision)

    @on(Button.Pressed, "#resume")
    def resume(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "正在从真实 Checkpoint 创建 continuation……")
        self._execute_resume()

    @on(Button.Pressed, "#pause")
    def pause(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "正在请求下一安全边界暂停并保存 Checkpoint……")
        self._execute_pause()

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "正在请求下一安全边界取消；已有事实不会回滚……")
        self._execute_cancel()

    @on(Button.Pressed, "#workbench")
    def open_workbench(self) -> None:
        current = self._controller.current
        if current is None or self._open_workbench is None:
            return
        self._set_busy(True, "正在打开只读 Workbench……")
        self._execute_open_workbench(current)

    @on(Button.Pressed, "#restart")
    def restart(self) -> None:
        if self._busy:
            return
        self._controller = GovernedShowcaseController(output_root=self._output_root)
        self.query_one("#timeline", RichLog).clear()
        self._reset_ready()

    @work(thread=True, group="governed-showcase", exclusive=True)
    def _execute_start(self) -> None:
        self._run_action("start", self._controller.start)

    @work(thread=True, group="governed-showcase", exclusive=True)
    def _execute_answer(self, answer: str) -> None:
        self._run_action("answer", lambda: self._controller.record_answer(answer))

    @work(thread=True, group="governed-showcase", exclusive=True)
    def _execute_decision(
        self,
        decision: Literal["approved", "rejected"],
    ) -> None:
        self._run_action("decision", lambda: self._controller.record_decision(decision))

    @work(thread=True, group="governed-showcase", exclusive=True)
    def _execute_resume(self) -> None:
        self._run_action("resume", self._controller.resume)

    @work(thread=True, group="governed-showcase", exclusive=True)
    def _execute_pause(self) -> None:
        self._run_action("pause", self._controller.pause_at_safe_boundary)

    @work(thread=True, group="governed-showcase", exclusive=True)
    def _execute_cancel(self) -> None:
        self._run_action("cancel", self._controller.cancel)

    @work(thread=True, group="workbench-open", exclusive=True)
    def _execute_open_workbench(
        self,
        result: ControlPlaneShowcaseResult,
    ) -> None:
        try:
            if self._open_workbench is not None:
                self._open_workbench(result)
        except Exception as exc:
            self.call_from_thread(self._show_workbench_error, exc)
            return
        self.call_from_thread(self._workbench_opened)

    def _run_action(
        self,
        label: str,
        action: Callable[[], ControlPlaneShowcaseResult],
    ) -> None:
        try:
            result = action()
        except Exception as exc:
            self.call_from_thread(self._show_error, exc)
            return
        self.call_from_thread(self._render_result, result)

    def _render_result(self, result: ControlPlaneShowcaseResult) -> None:
        self._set_busy(False)
        status = result.status
        self.query_one("#state", Static).update(
            Text(
                "\n".join(
                    [
                        f"状态：{status.upper()}",
                        f"Run：{result.run_dir.name}",
                    ]
                ),
                overflow="fold",
                no_wrap=False,
            )
        )
        durable_paths = _readable_durable_paths(result)
        self.query_one("#persistence", Static).update(
            Text(
                "本步权威对象 · 详情见 showcase.json\n"
                f"{durable_paths or '• 尚无'}",
                overflow="fold",
                no_wrap=False,
            )
        )
        if result.action == "human_input_recorded":
            self._show_actions("resume")
            self.query_one("#decision", Static).update(
                "人工输入已保存：pending → responded。\n"
                "workspace 未改变；检查 HumanInput JSON 后显式 Resume。"
            )
            self._timeline("HUMAN_INPUT_RECORDED · 回答已落盘，等待显式 Resume")
            return
        if result.action == "approval_recorded":
            self._show_actions("resume")
            self.query_one("#decision", Static).update(
                "Approval JSON 已保存人工决定。\n"
                "Ledger 与 workspace 尚未执行；"
                "检查 JSON 后再 Resume、Pause 或 Cancel。"
            )
            self._timeline("APPROVAL_RECORDED · 决定已落盘，真实写工具尚未执行")
            return
        if status == "waiting_human":
            self._show_actions("human")
            self.query_one("#decision", Static).update(
                "Agent 等待一条人工变更要求。\n"
                "保存时只写 HumanInput JSON，不改 workspace。\n"
                "检查 JSON 后再显式 Resume。"
            )
            self.query_one("#human-answer", Input).focus()
            self._timeline("WAITING_HUMAN · 问题已持久化，代码尚未改变")
        elif status == "waiting_approval":
            self._show_actions("approval")
            current = (result.workspace / "operator_request.txt").read_text(
                encoding="utf-8"
            ).strip()
            self.query_one("#decision", Static).update(
                "\n".join(
                    [
                        "人工输入已由 continuation 加载。",
                        f"当前文件：{current}",
                        "待执行：写入 operator_request.txt",
                        "本步只保存审批；Resume 后才执行。",
                    ]
                )
            )
            self._timeline("RESUME · continuation 已加载 HumanInput 回答")
            self._timeline("WAITING_APPROVAL · 补丁已登记，真实写工具尚未执行")
        elif status == "paused":
            self._show_actions("resume")
            self.query_one("#decision", Static).update(
                "RunControl 已在安全边界保存 paused Checkpoint。\n"
                "检查 task_state JSON 的 status/stop_reason，"
                "再 Resume 或 Cancel。"
            )
            self._timeline("PAUSED · 安全边界已持久化，可从同一 Checkpoint 恢复")
        else:
            self._show_actions("terminal")
            current = (result.workspace / "operator_request.txt").read_text(
                encoding="utf-8"
            ).strip()
            if status == "cancelled":
                outcome = "操作员取消任务；已有 Evidence 保留，workspace 保持原状。"
            elif current == GOVERNED_PLACEHOLDER:
                outcome = "操作员拒绝补丁；workspace 保持原状。"
            else:
                outcome = "补丁已执行，focused pytest 已形成验证证据。"
            self.query_one("#decision", Static).update(
                f"{outcome}\n"
                "最终文件：workspace/operator_request.txt\n"
                "Trace 与 Story 路径见 showcase.json。"
            )
            self._timeline("APPROVAL · 人工决定已绑定到原 operation fingerprint")
            self._timeline(f"{status.upper()} · {outcome}")

    def _show_actions(self, mode: str) -> None:
        self.query_one("#start", Button).display = mode == "ready"
        self.query_one("#human-input-actions", Horizontal).display = mode == "human"
        self.query_one("#approval-actions", Horizontal).display = mode == "approval"
        self.query_one("#resume-actions", Horizontal).display = mode in {
            "resume",
            "paused",
        }
        self.query_one("#terminal-actions", Horizontal).display = mode == "terminal"

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        for button in self.query(Button):
            button.disabled = busy
        for input_widget in self.query(Input):
            input_widget.disabled = busy
        if message:
            self.query_one("#state", Static).update(message)

    def _show_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self.query_one("#decision", Static).update(
            Text(f"ERROR\n\n{type(exc).__name__}: {exc}")
        )
        self._timeline(f"ERROR · {type(exc).__name__}: {exc}")

    def _workbench_opened(self) -> None:
        self._set_busy(False)
        self._timeline("WORKBENCH · 已打开同一份只读 Evidence")

    def _show_workbench_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self._timeline(
            f"WORKBENCH ERROR · {type(exc).__name__}: {exc}；运行证据仍已保留"
        )

    def _timeline(self, message: str) -> None:
        self.query_one("#timeline", RichLog).write(Text(message))


def run_governed_showcase_console(
    *,
    output_root: str | Path,
    open_workbench: OpenWorkbench | None = None,
) -> None:
    """启动按钮式 Lab 1；退出界面不会删除任何已生成证据。"""

    GovernedShowcaseConsoleApp(
        output_root=output_root,
        open_workbench=open_workbench,
    ).run()


__all__ = ["GovernedShowcaseConsoleApp", "run_governed_showcase_console"]
