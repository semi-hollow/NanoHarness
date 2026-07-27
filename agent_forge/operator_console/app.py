"""Textual 实现的 NanoHarness Operator Console。

这是展示层文件。理解 Agent 主链时无需阅读；所有真实能力都通过
``OperatorSession`` 进入 Harness。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    TextArea,
)

from agent_forge.harness import RunResult
from agent_forge.operator_console.api import (
    OperatorSessionBundle,
    build_operator_session,
)
from agent_forge.observability.domain.live_event import RuntimeEvent
from agent_forge.operator_console.events import render_event, should_render_event
from agent_forge.operator_console.session import OperatorPrompt, OperatorSession
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus


class OperatorConsoleApp(App[None]):
    """把真实 Runtime 事件和人工控制投影为单屏操作台。"""

    TITLE = "NanoHarness Operator Console"
    SUB_TITLE = "真实 AgentLoop · 实时事件 · 人工控制 · Durable Resume"
    CSS = """
    Screen {
        background: #111416;
        color: #e8ecef;
    }

    Header {
        background: #171b1e;
        color: #f4f6f7;
    }

    #launch {
        height: 12;
        padding: 1 2;
        border-bottom: solid #3c464d;
    }

    #workspace {
        width: 2fr;
        margin-right: 1;
    }

    #launch-actions {
        width: 1fr;
        align: right middle;
    }

    #task {
        height: 6;
        margin-top: 1;
        border: solid #465159;
        background: #171b1e;
    }

    #main {
        height: 1fr;
    }

    #timeline-pane {
        width: 2fr;
        border-right: solid #3c464d;
    }

    #timeline-title, #control-title {
        height: 2;
        padding: 0 2;
        color: #a9d8b8;
        text-style: bold;
    }

    #timeline-header {
        height: 3;
        padding: 0 1 0 0;
        align: left middle;
    }

    #timeline-title {
        width: 1fr;
    }

    #timeline-mode {
        width: 16;
        min-width: 16;
        height: 3;
        margin: 0;
        background: #30383d;
    }

    #timeline {
        height: 1fr;
        padding: 0 2 1 2;
        background: #111416;
        scrollbar-color: #5d6b73;
    }

    #control-pane {
        width: 1fr;
        min-width: 38;
        padding: 0 2 1 2;
    }

    #status {
        height: 7;
        padding: 1;
        border: solid #465159;
        background: #171b1e;
        overflow-y: auto;
    }

    #prompt {
        height: 1fr;
        min-height: 4;
        margin-top: 1;
        padding: 1;
        border: solid #7b6a3a;
        background: #181713;
        overflow-y: auto;
    }

    #operator-input {
        margin-top: 1;
    }

    #operator-actions {
        height: 3;
        margin-top: 1;
    }

    Button {
        min-width: 9;
        margin-right: 1;
        border: none;
    }

    Button.-primary {
        background: #247a4a;
    }

    #reject, #cancel {
        background: #8b3a3a;
    }

    #pause, #resume {
        background: #8a6d1d;
    }

    #approve {
        background: #247a4a;
    }

    Footer {
        background: #171b1e;
    }
    """
    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("f6", "pause_run", "暂停"),
        ("f8", "cancel_run", "取消"),
    ]

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self._args = args
        self._bundle: OperatorSessionBundle | None = None
        self._busy = False
        self._event_history: list[RuntimeEvent] = []
        self._show_infrastructure_events = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="launch"):
            with Horizontal():
                yield Input(
                    value=str(getattr(self._args, "workspace", "") or Path.cwd()),
                    placeholder="Repository workspace",
                    id="workspace",
                )
                with Horizontal(id="launch-actions"):
                    yield Button("运行", id="start", variant="primary")
                    yield Button("恢复最近", id="attach-latest")
            yield TextArea(
                str(getattr(self._args, "task", "") or ""),
                language="markdown",
                id="task",
            )
        with Horizontal(id="main"):
            with Vertical(id="timeline-pane"):
                with Horizontal(id="timeline-header"):
                    yield Label("Execution Timeline · 主流程", id="timeline-title")
                    yield Button("显示底层事件", id="timeline-mode")
                yield RichLog(id="timeline", highlight=True, markup=False, wrap=True)
            with Vertical(id="control-pane"):
                yield Label("Runtime Control", id="control-title")
                yield Static(
                    "状态：READY\n等待输入任务并点击“运行”。",
                    id="status",
                )
                yield Static(
                    "这里显示当前 Checkpoint、人工问题、审批目标和最终 Artifact。",
                    id="prompt",
                )
                yield Input(
                    placeholder="运行中输入 steer；等待人工时输入回答",
                    id="operator-input",
                    disabled=True,
                )
                with Horizontal(id="operator-actions"):
                    yield Button("发送", id="send", disabled=True)
                    yield Button("批准", id="approve")
                    yield Button("拒绝", id="reject")
                    yield Button("继续", id="resume")
                    yield Button("暂停", id="pause", disabled=True)
                    yield Button("取消", id="cancel", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#approve", Button).display = False
        self.query_one("#reject", Button).display = False
        self.query_one("#resume", Button).display = False
        self.set_interval(0.10, self._drain_runtime_events)
        self.query_one("#task", TextArea).focus()

    @on(Button.Pressed, "#start")
    def start_run(self) -> None:
        task = self.query_one("#task", TextArea).text.strip()
        workspace = self.query_one("#workspace", Input).value.strip()
        if not task:
            self._show_error("任务不能为空。")
            return
        if not workspace:
            self._show_error("Workspace 不能为空。")
            return
        self.query_one("#timeline", RichLog).clear()
        self._event_history.clear()
        self.query_one("#launch", Vertical).display = False
        self._set_busy(True, "正在装配 Runtime...")
        self._execute_start(task, workspace)

    @on(Button.Pressed, "#attach-latest")
    def attach_latest(self) -> None:
        workspace = self.query_one("#workspace", Input).value.strip()
        if not workspace:
            self._show_error("Workspace 不能为空。")
            return
        self.query_one("#launch", Vertical).display = False
        self._set_busy(True, "正在读取最近一次 durable checkpoint...")
        self._execute_attach_latest(workspace)

    @on(Button.Pressed, "#send")
    @on(Input.Submitted, "#operator-input")
    def submit_operator_input(self) -> None:
        session = self._session()
        value = self.query_one("#operator-input", Input).value.strip()
        if not value:
            self._show_error("输入不能为空。")
            return
        prompt = session.pending_prompt()
        self.query_one("#operator-input", Input).value = ""
        if prompt is not None and prompt.kind == "human_input":
            self._set_busy(True, "正在保存人工回答并自动续跑...")
            self._execute_answer(value)
            return
        session.steer(value)
        self._timeline_message(f"操作员 steer 已排队：{value}")

    @on(Button.Pressed, "#approve")
    def approve(self) -> None:
        self._set_busy(True, "审批已批准，正在自动续跑...")
        self._execute_decision("approved")

    @on(Button.Pressed, "#reject")
    def reject(self) -> None:
        self._set_busy(True, "审批已拒绝，正在自动续跑...")
        self._execute_decision("rejected")

    @on(Button.Pressed, "#resume")
    def resume(self) -> None:
        self._set_busy(True, "正在从 durable checkpoint 继续...")
        self._execute_resume()

    @on(Button.Pressed, "#pause")
    def pause(self) -> None:
        self.action_pause_run()

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.action_cancel_run()

    @on(Button.Pressed, "#timeline-mode")
    def toggle_timeline_mode(self) -> None:
        """在主流程和底层事件之间切换，并重放当前会话历史。"""

        self._show_infrastructure_events = not self._show_infrastructure_events
        button = self.query_one("#timeline-mode", Button)
        title = self.query_one("#timeline-title", Label)
        if self._show_infrastructure_events:
            button.label = "只看主流程"
            title.update("Execution Timeline · 全部事件")
        else:
            button.label = "显示底层事件"
            title.update("Execution Timeline · 主流程")
        self._render_event_history()

    def action_pause_run(self) -> None:
        if self._bundle is None or not self._busy:
            return
        self._bundle.session.pause()
        self._timeline_message("已请求暂停；将在下一个 Runtime 安全边界生效。")

    def action_cancel_run(self) -> None:
        if self._bundle is None or not self._busy:
            return
        self._bundle.session.cancel()
        self._timeline_message("已请求取消；既有副作用不会自动回滚。")

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_start(self, task: str, workspace: str) -> None:
        try:
            bundle = build_operator_session(
                self._args,
                task=task,
                workspace=workspace,
            )
            self._bundle = bundle
            result = bundle.session.start()
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, result)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_attach_latest(self, workspace: str) -> None:
        try:
            bundle = build_operator_session(
                self._args,
                task="continue the latest durable NanoHarness run",
                workspace=workspace,
            )
            self._bundle = bundle
            checkpoint = bundle.session.attach_latest(workspace)
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_attachment, checkpoint)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_answer(self, answer: str) -> None:
        try:
            result = self._session().answer_and_resume(answer)
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, result)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_decision(
        self,
        decision: Literal["approved", "rejected"],
    ) -> None:
        try:
            result = self._session().decide_and_resume(decision)
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, result)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_resume(self) -> None:
        try:
            result = self._session().resume()
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, result)

    def _finish_result(self, result: RunResult) -> None:
        self._set_busy(False)
        self._render_checkpoint(result.checkpoint, result.artifact_dir)
        self._render_operator_prompt()
        if not result.waiting_for_operator:
            self._show_run_evidence(result)

    def _finish_attachment(self, checkpoint: TaskCheckpoint) -> None:
        self._set_busy(False)
        artifact_dir = self._session().artifact_dir
        self._render_checkpoint(checkpoint, artifact_dir)
        self._render_operator_prompt()
        self._timeline_message(
            f"已接管 checkpoint：{checkpoint.run_id} ({checkpoint.status})"
        )

    def _finish_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self.query_one("#launch", Vertical).display = True
        self._show_error(f"{type(exc).__name__}: {exc}")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.query_one("#start", Button).disabled = busy
        self.query_one("#attach-latest", Button).disabled = busy
        self.query_one("#pause", Button).disabled = not busy
        self.query_one("#cancel", Button).disabled = not busy
        operator_input = self.query_one("#operator-input", Input)
        operator_input.disabled = self._bundle is None
        self.query_one("#send", Button).disabled = self._bundle is None
        if message:
            self.query_one("#status", Static).update(message)

    def _render_checkpoint(
        self,
        checkpoint: TaskCheckpoint,
        artifact_dir: Path | None,
    ) -> None:
        lines = [
            f"状态：{checkpoint.status.upper()}",
            f"Step：{checkpoint.current_step}",
            f"停止原因：{checkpoint.stop_reason or '-'}",
            f"消息 / 观察：{checkpoint.messages_count} / "
            f"{checkpoint.observations_count}",
            f"Checkpoint：{checkpoint.run_id}",
        ]
        usage = self._usage_summary(artifact_dir)
        if usage:
            lines.append(usage)
        if artifact_dir is not None:
            lines.append(f"Artifact：{artifact_dir}")
        self.query_one("#status", Static).update(Text("\n".join(lines)))

    def _render_operator_prompt(self) -> None:
        prompt = self._session().pending_prompt()
        approve = self.query_one("#approve", Button)
        reject = self.query_one("#reject", Button)
        resume = self.query_one("#resume", Button)
        approve.display = bool(prompt and prompt.kind == "approval")
        reject.display = bool(prompt and prompt.kind == "approval")
        checkpoint = self._session().checkpoint
        resume.display = bool(
            checkpoint is not None and checkpoint.status == TaskRunStatus.PAUSED.value
        )
        if prompt is None:
            self.query_one("#prompt", Static).update(
                "当前没有待处理的人工问题或审批。\n"
                "运行中可在下方输入 steer，方向会在下一次模型边界生效。"
            )
            return
        self.query_one("#prompt", Static).update(Text(self._prompt_text(prompt)))
        operator_input = self.query_one("#operator-input", Input)
        if prompt.kind == "human_input" and prompt.choices:
            operator_input.placeholder = f"输入可选值：{', '.join(prompt.choices)}"
        elif prompt.kind == "human_input":
            operator_input.placeholder = "输入回答后按 Enter"
        else:
            operator_input.placeholder = "审批请使用“批准”或“拒绝”"
        operator_input.disabled = prompt.kind != "human_input"
        self.query_one("#send", Button).disabled = prompt.kind != "human_input"
        if prompt.kind == "human_input":
            operator_input.focus()

    def _drain_runtime_events(self) -> None:
        if self._bundle is None:
            return
        try:
            timeline = self.query_one("#timeline", RichLog)
        except NoMatches:
            return
        for event in self._bundle.events.drain():
            self._event_history.append(event)
            if should_render_event(
                event,
                include_infrastructure=self._show_infrastructure_events,
            ):
                timeline.write(
                    Text(
                        render_event(
                            event,
                            include_infrastructure=(self._show_infrastructure_events),
                        )
                    )
                )

    def _render_event_history(self) -> None:
        """按当前视图重放事件；只保留有限历史，避免长任务拖慢 TUI。"""

        if len(self._event_history) > 2_000:
            self._event_history = self._event_history[-2_000:]
        timeline = self.query_one("#timeline", RichLog)
        timeline.clear()
        for event in self._event_history:
            if not should_render_event(
                event,
                include_infrastructure=self._show_infrastructure_events,
            ):
                continue
            timeline.write(
                Text(
                    render_event(
                        event,
                        include_infrastructure=self._show_infrastructure_events,
                    )
                )
            )

    def _show_run_evidence(self, result: RunResult) -> None:
        """终态直接展示关键 artifact，避免现场再翻 JSON。"""

        sections = [
            f"Run Result: {result.status.value.upper()}",
            "",
            result.final_answer or "(empty final answer)",
        ]
        candidate_diff_preview = self._read_preview(
            result.candidate_diff_path,
            max_chars=2_400,
        )
        if candidate_diff_preview:
            sections.extend(
                [
                    "",
                    "Candidate Patch (不等于 official solved)",
                    candidate_diff_preview,
                ]
            )
        sections.extend(
            [
                "",
                "Evidence",
                f"artifact: {result.artifact_dir}",
                f"trace: {result.trace_path or '-'}",
                f"usage: {result.usage_path or '-'}",
            ]
        )
        self.query_one("#prompt", Static).update(Text("\n".join(sections)))

    def _show_error(self, message: str) -> None:
        self.query_one("#prompt", Static).update(Text(f"错误\n\n{message}"))
        self._timeline_message(f"ERROR: {message}")

    def _timeline_message(self, message: str) -> None:
        self.query_one("#timeline", RichLog).write(Text(message))

    def _session(self) -> OperatorSession:
        if self._bundle is None:
            raise RuntimeError("请先运行或接管一个 NanoHarness 会话")
        return self._bundle.session

    @staticmethod
    def _prompt_text(prompt: OperatorPrompt) -> str:
        lines = [prompt.title, "", prompt.body]
        if prompt.choices:
            lines.extend(["", f"可选值：{', '.join(prompt.choices)}"])
        if prompt.details:
            lines.extend(["", "Evidence", prompt.details])
        return "\n".join(lines)

    @staticmethod
    def _usage_summary(artifact_dir: Path | None) -> str:
        if artifact_dir is None:
            return ""
        path = artifact_dir / "usage.json"
        if not path.is_file():
            return ""
        try:
            summary = json.loads(path.read_text(encoding="utf-8")).get("summary", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            return ""
        return (
            "Usage："
            f"{summary.get('llm_calls', 0)} LLM / "
            f"{summary.get('tool_calls', 0)} tools / "
            f"{summary.get('total_tokens', 0)} tokens / "
            f"${float(summary.get('estimated_cost_usd', 0) or 0):.4f}"
        )

    @staticmethod
    def _read_preview(path: Path | None, *, max_chars: int) -> str:
        if path is None or not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        if len(text) <= max_chars:
            return text
        return f"{text[: max_chars - 1]}…"
