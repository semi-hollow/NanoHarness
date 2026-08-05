"""Textual 实现的 NanoHarness Operator Console。

这是展示层文件。理解 Agent 主链时无需阅读；所有真实能力都通过
``OperatorSession`` 进入 Harness。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TextArea,
)

from agent_forge.context.api import (
    forget_memory,
    list_memories,
    RememberMemoryRequest,
    remember_memory,
)
from agent_forge.context.domain import LongTermMemoryRecord
from agent_forge.harness import RunResult
from agent_forge.operator_console.api import (
    OperatorSessionBundle,
    build_operator_session,
    build_task_session_library,
    resolve_operator_memory_root,
)
from agent_forge.operator_console.application import TaskSessionLibrary
from agent_forge.operator_console.domain import TaskSession
from agent_forge.observability.domain.live_event import RuntimeEvent
from agent_forge.operator_console.events import render_event, should_render_event
from agent_forge.operator_console.session import OperatorPrompt, OperatorSession
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus


class LongTermMemoryScreen(ModalScreen[None]):
    """管理用户显式授权的跨 Run 记忆。

    这是一个展示层适配器：它只调用 Context 公共 API，不修改
    已启动 Run 的 ``WorkingMemory`` 快照。
    """

    CSS = """
    LongTermMemoryScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.62);
    }

    #memory-dialog {
        width: 92;
        height: 35;
        padding: 1 2;
        border: solid #64717a;
        background: #171b1e;
    }

    #memory-heading {
        height: 2;
        color: #a9d8b8;
        text-style: bold;
    }

    #memory-help, #memory-feedback {
        height: 3;
        color: #b9c3c9;
    }

    #memory-records, #memory-scope, #memory-key {
        margin-bottom: 1;
    }

    #memory-content {
        height: 8;
        margin-bottom: 1;
        border: solid #465159;
        background: #111416;
    }

    #memory-actions {
        height: 3;
        align: right middle;
    }

    #memory-forget {
        background: #8b3a3a;
    }
    """

    def __init__(
        self,
        *,
        memory_root: Path,
        workspace: str,
    ) -> None:
        super().__init__()
        self._memory_root = memory_root
        self._workspace = workspace
        self._records_by_id: dict[str, LongTermMemoryRecord] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-dialog"):
            yield Label("长期记忆", id="memory-heading")
            yield Static(
                "只有你显式保存的内容才会进入长期记忆；修改从下一个 Run 生效。",
                id="memory-help",
            )
            yield Select([], prompt="选择已有记忆（可选）", id="memory-records")
            yield Select(
                [("当前项目", "project"), ("用户全局", "user")],
                value="project",
                allow_blank=False,
                id="memory-scope",
            )
            yield Input(
                placeholder="Key，例如 response.style",
                id="memory-key",
            )
            yield TextArea(
                language="markdown",
                id="memory-content",
            )
            yield Static("填写 key 和内容后保存。", id="memory-feedback")
            with Horizontal(id="memory-actions"):
                yield Button("记住 / 更新", id="memory-remember", variant="primary")
                yield Button("忘记选中项", id="memory-forget")
                yield Button("关闭", id="memory-close")

    def on_mount(self) -> None:
        self._refresh_records()
        self.query_one("#memory-key", Input).focus()

    @on(Select.Changed, "#memory-records")
    def load_selected_memory(self, event: Select.Changed) -> None:
        """把选中记忆回填到编辑区，让更新与新增共用同一入口。"""

        memory_id = event.value if isinstance(event.value, str) else ""
        memory_record = self._records_by_id.get(memory_id)
        if memory_record is None:
            return
        self.query_one("#memory-scope", Select).value = memory_record.scope
        self.query_one("#memory-key", Input).value = memory_record.key
        self.query_one("#memory-content", TextArea).text = memory_record.content
        self.query_one("#memory-feedback", Static).update(
            f"已选中 {memory_record.key} · revision {memory_record.revision}"
        )

    @on(Button.Pressed, "#memory-remember")
    def remember(self) -> None:
        """显式 upsert：同 scope/key 更新 revision，不生成重复记录。"""

        scope_value = self.query_one("#memory-scope", Select).value
        scope = scope_value if isinstance(scope_value, str) else "project"
        key = self.query_one("#memory-key", Input).value.strip()
        content = self.query_one("#memory-content", TextArea).text.strip()
        try:
            record = remember_memory(
                RememberMemoryRequest(
                    memory_root=str(self._memory_root),
                    workspace=self._workspace,
                    key=key,
                    content=content,
                    scope=scope,
                )
            )
        except ValueError as exc:
            self.query_one("#memory-feedback", Static).update(f"无法保存：{exc}")
            return
        self._refresh_records(selected_memory_id=record.memory_id)
        self.query_one("#memory-feedback", Static).update(
            f"已保存 {record.key} · revision {record.revision}；下一个 Run 生效。"
        )

    @on(Button.Pressed, "#memory-forget")
    def forget(self) -> None:
        """物理删除选中记忆；已启动 Run 仍使用旧快照。"""

        selected_value = self.query_one("#memory-records", Select).value
        memory_id = selected_value if isinstance(selected_value, str) else ""
        if not memory_id:
            self.query_one("#memory-feedback", Static).update("请先选择要忘记的记忆。")
            return
        try:
            forgotten = forget_memory(str(self._memory_root), memory_id)
        except ValueError as exc:
            self.query_one("#memory-feedback", Static).update(f"无法忘记：{exc}")
            return
        self.query_one("#memory-key", Input).value = ""
        self.query_one("#memory-content", TextArea).text = ""
        self._refresh_records()
        self.query_one("#memory-feedback", Static).update(
            f"已忘记 {forgotten.key}；下一个 Run 不再召回。"
        )

    @on(Button.Pressed, "#memory-close")
    def close(self) -> None:
        self.dismiss(None)

    def _refresh_records(self, *, selected_memory_id: str = "") -> None:
        records = list_memories(
            str(self._memory_root),
            self._workspace,
        )
        self._records_by_id = {record.memory_id: record for record in records}
        selector = self.query_one("#memory-records", Select)
        selector.set_options(
            [
                (
                    f"{record.scope} · {record.key} · r{record.revision}",
                    record.memory_id,
                )
                for record in records
            ]
        )
        if selected_memory_id:
            selector.value = selected_memory_id
        else:
            selector.clear()


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
        height: 20;
        padding: 1 2;
        border-bottom: solid #3c464d;
    }

    #session-row, #workspace-row, #session-actions {
        height: 3;
    }

    #session-picker {
        width: 2fr;
        margin-right: 1;
    }

    #session-title {
        width: 1fr;
    }

    #session-summary {
        height: 4;
        padding: 0 1;
        color: #b9c3c9;
        overflow-y: auto;
    }

    #workspace {
        width: 2fr;
        margin-right: 1;
    }

    #launch-actions, #session-actions {
        width: 1fr;
        align: right middle;
    }

    #task {
        height: 6;
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

    #sessions {
        width: 10;
        min-width: 10;
        height: 3;
        margin: 0 1 0 0;
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

    #prompt-scroll {
        height: 1fr;
        min-height: 4;
        margin-top: 1;
        border: solid #7b6a3a;
        background: #181713;
        scrollbar-color: #8b7b47;
    }

    #prompt {
        height: auto;
        padding: 1;
        background: #181713;
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

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        task_sessions: TaskSessionLibrary | None = None,
    ) -> None:
        super().__init__()
        self._args = args
        self._task_sessions = task_sessions or build_task_session_library(args)
        self._selected_task_session_id = ""
        self._bundle: OperatorSessionBundle | None = None
        self._busy = False
        self._event_history: list[RuntimeEvent] = []
        self._show_infrastructure_events = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="launch"):
            with Horizontal(id="session-row"):
                yield Select(
                    self._session_options(),
                    prompt="新建任务会话",
                    id="session-picker",
                )
                yield Input(
                    placeholder="会话名称（可选，可随时重命名）",
                    id="session-title",
                )
            with Horizontal(id="session-actions"):
                yield Button("新会话", id="new-session")
                yield Button("打开", id="open-session", variant="primary")
                yield Button("重命名", id="rename-session")
                yield Button("置顶", id="pin-session")
                yield Button("归档", id="archive-session")
            yield Static(
                "选择历史会话可接管最新 Checkpoint；新任务则填写下面两项。",
                id="session-summary",
            )
            with Horizontal(id="workspace-row"):
                yield Input(
                    value=str(getattr(self._args, "workspace", "") or Path.cwd()),
                    placeholder="Repository workspace",
                    id="workspace",
                )
                with Horizontal(id="launch-actions"):
                    yield Button("长期记忆", id="memory")
                    yield Button("运行新会话", id="start", variant="primary")
            yield TextArea(
                str(getattr(self._args, "task", "") or ""),
                language="markdown",
                id="task",
            )
        with Horizontal(id="main"):
            with Vertical(id="timeline-pane"):
                with Horizontal(id="timeline-header"):
                    yield Label("Execution Timeline · 主流程", id="timeline-title")
                    yield Button("会话库", id="sessions")
                    yield Button("显示底层事件", id="timeline-mode")
                yield RichLog(id="timeline", highlight=True, markup=False, wrap=True)
            with Vertical(id="control-pane"):
                yield Label("Runtime Control", id="control-title")
                yield Static(
                    "状态：READY\n等待输入任务并点击“运行”。",
                    id="status",
                )
                with VerticalScroll(id="prompt-scroll", can_focus=True):
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
        self._refresh_session_picker()
        self.set_interval(0.10, self._drain_runtime_events)
        self.query_one("#task", TextArea).focus()

    @on(Select.Changed, "#session-picker")
    def select_task_session(self, event: Select.Changed) -> None:
        """选择只更新预览；真正加载 checkpoint 仍需点击“打开”。"""

        self._selected_task_session_id = (
            event.value if isinstance(event.value, str) else ""
        )
        self._render_selected_session_summary()

    @on(Button.Pressed, "#new-session")
    def prepare_new_session(self) -> None:
        if self._busy:
            return
        self._selected_task_session_id = ""
        self.query_one("#session-picker", Select).clear()
        self.query_one("#session-title", Input).value = ""
        self.query_one("#workspace", Input).value = str(
            getattr(self._args, "workspace", "") or Path.cwd()
        )
        self.query_one("#task", TextArea).text = str(
            getattr(self._args, "task", "") or ""
        )
        self.query_one("#session-summary", Static).update(
            "新会话会获得稳定 Session ID；每次执行仍生成独立 Run 和证据目录。"
        )
        self._bundle = None
        self.query_one("#task", TextArea).focus()

    @on(Button.Pressed, "#open-session")
    def open_selected_session(self) -> None:
        session_id = self._selected_task_session_id
        if not session_id:
            self._show_error("请先选择一个历史会话。")
            return
        self.query_one("#timeline", RichLog).clear()
        self._event_history.clear()
        self.query_one("#launch", Vertical).display = False
        self._set_busy(True, "正在接管该会话最近一次 durable checkpoint...")
        self._execute_attach_session(session_id)

    @on(Button.Pressed, "#rename-session")
    def rename_selected_session(self) -> None:
        session_id = self._selected_task_session_id
        title = self.query_one("#session-title", Input).value.strip()
        if not session_id or not title:
            self._show_error("请选择会话并输入新的名称。")
            return
        self._task_sessions.rename(session_id, title)
        self._refresh_session_picker(selected_session_id=session_id)
        self._render_selected_session_summary()

    @on(Button.Pressed, "#pin-session")
    def pin_selected_session(self) -> None:
        session_id = self._selected_task_session_id
        if not session_id:
            self._show_error("请先选择一个会话。")
            return
        session = self._task_sessions.toggle_pinned(session_id)
        self._refresh_session_picker(selected_session_id=session_id)
        self.query_one("#pin-session", Button).label = (
            "取消置顶" if session.pinned else "置顶"
        )

    @on(Button.Pressed, "#archive-session")
    def archive_selected_session(self) -> None:
        session_id = self._selected_task_session_id
        if not session_id:
            self._show_error("请先选择一个会话。")
            return
        self._task_sessions.set_archived(session_id)
        self._selected_task_session_id = ""
        self._refresh_session_picker()
        self.query_one("#session-summary", Static).update(
            "会话已归档；Run、Workspace 和证据均未删除。"
        )

    @on(Button.Pressed, "#sessions")
    def show_session_library(self) -> None:
        if self._busy:
            self._show_error("当前 Run 仍在执行；请先暂停或等待到达终态。")
            return
        self._refresh_session_picker(
            selected_session_id=(
                self._bundle.task_session_id if self._bundle is not None else ""
            )
        )
        self.query_one("#launch", Vertical).display = True

    @on(Button.Pressed, "#start")
    def start_run(self) -> None:
        task = self.query_one("#task", TextArea).text.strip()
        workspace = self.query_one("#workspace", Input).value.strip()
        session_title = self.query_one("#session-title", Input).value.strip()
        if self._selected_task_session_id:
            self._show_error("历史会话请点击“打开”；新任务请先点击“新会话”。")
            return
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
        self._execute_start(task, workspace, session_title)

    @on(Button.Pressed, "#memory")
    def show_long_term_memory(self) -> None:
        """从当前 workspace 打开记忆管理；不干扰正在运行的快照。"""

        workspace = self.query_one("#workspace", Input).value.strip()
        if not workspace:
            self._show_error("请先填写 Workspace。")
            return
        memory_root = resolve_operator_memory_root(self._args, workspace)
        self.push_screen(
            LongTermMemoryScreen(
                memory_root=memory_root,
                workspace=workspace,
            )
        )

    @on(Button.Pressed, "#send")
    @on(Input.Submitted, "#operator-input")
    def submit_operator_input(self) -> None:
        session = self._session()
        operator_input = self.query_one("#operator-input", Input).value.strip()
        if not operator_input:
            self._show_error("输入不能为空。")
            return
        pending_operator_prompt = session.pending_prompt()
        self.query_one("#operator-input", Input).value = ""
        if (
            pending_operator_prompt is not None
            and pending_operator_prompt.kind == "human_input"
        ):
            self._set_busy(True, "正在保存人工回答并自动续跑...")
            self._execute_answer(operator_input)
            return
        checkpoint = session.checkpoint
        if (
            not self._busy
            and checkpoint is not None
            and checkpoint.status
            in {
                TaskRunStatus.COMPLETED.value,
                TaskRunStatus.BLOCKED.value,
                TaskRunStatus.FAILED.value,
                TaskRunStatus.CANCELLED.value,
            }
        ):
            self._set_busy(True, "正在同一任务会话中创建后续 Run...")
            self._execute_follow_up(operator_input)
            return
        session.steer(operator_input)
        self._timeline_message(f"操作员 steer 已排队：{operator_input}")

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
    def _execute_start(self, task: str, workspace: str, session_title: str) -> None:
        try:
            bundle = build_operator_session(
                self._args,
                task=task,
                workspace=workspace,
                session_title=session_title,
                task_sessions=self._task_sessions,
            )
            self._bundle = bundle
            run_result = bundle.session.start()
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, run_result)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_attach_session(self, task_session_id: str) -> None:
        try:
            task_session = self._task_sessions.require(task_session_id)
            latest_run = task_session.latest_run
            if latest_run is None:
                raise RuntimeError("该会话还没有可接管的 Run")
            bundle = build_operator_session(
                self._args,
                task=latest_run.task or task_session.initial_task,
                workspace=task_session.workspace,
                task_session_id=task_session_id,
                task_sessions=self._task_sessions,
            )
            self._bundle = bundle
            checkpoint = bundle.session.attach_run(latest_run.artifact_dir)
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_attachment, checkpoint)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_follow_up(self, message: str) -> None:
        try:
            run_result = self._session().continue_with_user_message(message)
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, run_result)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_answer(self, answer: str) -> None:
        try:
            run_result = self._session().answer_and_resume(answer)
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, run_result)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_decision(
        self,
        decision: Literal["approved", "rejected"],
    ) -> None:
        try:
            run_result = self._session().decide_and_resume(decision)
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, run_result)

    @work(thread=True, group="runtime", exclusive=True)
    def _execute_resume(self) -> None:
        try:
            run_result = self._session().resume()
        except Exception as exc:
            self.call_from_thread(self._finish_error, exc)
            return
        self.call_from_thread(self._finish_result, run_result)

    def _finish_result(self, run_result: RunResult) -> None:
        self._set_busy(False)
        if self._bundle is not None:
            self._selected_task_session_id = self._bundle.task_session_id
            self._refresh_session_picker(
                selected_session_id=self._bundle.task_session_id
            )
        self._render_checkpoint(run_result.checkpoint, run_result.artifact_dir)
        self._render_operator_prompt()
        if not run_result.waiting_for_operator:
            self._show_run_evidence(run_result)

    def _finish_attachment(self, checkpoint: TaskCheckpoint) -> None:
        self._set_busy(False)
        if self._bundle is not None:
            self._selected_task_session_id = self._bundle.task_session_id
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
        for selector in (
            "#new-session",
            "#open-session",
            "#rename-session",
            "#pin-session",
            "#archive-session",
            "#sessions",
            "#memory",
        ):
            self.query_one(selector, Button).disabled = busy
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
            checkpoint is not None
            and checkpoint.status
            in {
                TaskRunStatus.RUNNING.value,
                TaskRunStatus.PAUSED.value,
            }
        )
        if prompt is None:
            checkpoint = self._session().checkpoint
            operator_input = self.query_one("#operator-input", Input)
            if checkpoint is not None and checkpoint.status in {
                TaskRunStatus.RUNNING.value,
                TaskRunStatus.PAUSED.value,
            }:
                operator_input.placeholder = "先点击“继续”，恢复后才能发送 steer"
                operator_input.disabled = True
                self.query_one("#send", Button).disabled = True
                self.query_one("#prompt", Static).update(
                    "当前 Run 已停在 durable checkpoint。\n"
                    "点击“继续”会创建 continuation Run；不会恢复旧进程、HTTP 请求或 KV Cache。"
                )
                return
            if checkpoint is not None and checkpoint.status in {
                TaskRunStatus.COMPLETED.value,
                TaskRunStatus.BLOCKED.value,
                TaskRunStatus.FAILED.value,
                TaskRunStatus.CANCELLED.value,
            }:
                operator_input.placeholder = "输入后续要求，作为同一会话的新 Run"
            self.query_one("#prompt", Static).update(
                "当前没有待处理的人工问题或审批。\n"
                "运行中输入会成为 steer；终态后输入会创建同一 Session 的后续 Run。"
            )
            return
        self.query_one("#prompt", Static).update(Text(self._prompt_text(prompt)))
        prompt_scroll = self.query_one("#prompt-scroll", VerticalScroll)
        prompt_scroll.scroll_home(animate=False)
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
        else:
            prompt_scroll.focus()

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

    def _session_options(self) -> list[tuple[str, str]]:
        return [
            (self._session_option_label(session), session.session_id)
            for session in self._task_sessions.list_active()
        ]

    def _refresh_session_picker(self, *, selected_session_id: str = "") -> None:
        """刷新会话下拉框，同时尽量保留当前选择。"""

        picker = self.query_one("#session-picker", Select)
        picker.set_options(self._session_options())
        session_ids = {session.session_id for session in self._task_sessions.list_active()}
        desired = selected_session_id or self._selected_task_session_id
        if desired and desired in session_ids:
            self._selected_task_session_id = desired
            picker.value = desired
        else:
            self._selected_task_session_id = ""
            picker.clear()

    def _render_selected_session_summary(self) -> None:
        session_id = self._selected_task_session_id
        if not session_id:
            self.query_one("#session-summary", Static).update(
                "新会话会获得稳定 Session ID；每次执行仍生成独立 Run 和证据目录。"
            )
            self.query_one("#pin-session", Button).label = "置顶"
            return
        session = self._task_sessions.require(session_id)
        latest = session.latest_run
        self.query_one("#session-title", Input).value = session.title
        self.query_one("#workspace", Input).value = session.workspace
        self.query_one("#pin-session", Button).label = (
            "取消置顶" if session.pinned else "置顶"
        )
        if latest is None:
            summary = f"{session.title} · 尚未运行 · Workspace: {session.workspace}"
        else:
            updated = datetime.fromtimestamp(latest.updated_at).strftime("%m-%d %H:%M")
            summary = (
                f"{session.title} · {len(session.runs)} 次 Run · 最近 {updated}\n"
                f"状态 {latest.status.upper()} · Step {latest.current_step} · "
                f"{'可从 Checkpoint 继续' if latest.checkpoint_path else '无 Checkpoint'}"
            )
        self.query_one("#session-summary", Static).update(summary)

    @staticmethod
    def _session_option_label(session: TaskSession) -> str:
        latest = session.latest_run
        status = latest.status.upper() if latest is not None else "NEW"
        updated_at = latest.updated_at if latest is not None else session.updated_at
        updated = datetime.fromtimestamp(updated_at).strftime("%m-%d %H:%M")
        prefix = "★ " if session.pinned else ""
        return (
            f"{prefix}{session.title} · {status} · "
            f"{len(session.runs)} Runs · {updated}"
        )

    @staticmethod
    def _prompt_text(prompt: OperatorPrompt) -> str:
        lines = [prompt.title, "", prompt.body]
        if prompt.choices:
            lines.extend(["", f"可选值：{', '.join(prompt.choices)}"])
        if prompt.details:
            if prompt.kind == "approval":
                lines.extend(
                    [
                        "",
                        "审批前请完整核对目标、原内容和新内容。",
                        "可使用鼠标滚轮、方向键或 Page Up / Page Down 查看全部详情。",
                        f"Operation Key：{prompt.key}",
                        "",
                        OperatorConsoleApp._format_approval_details(prompt.details),
                    ]
                )
            else:
                lines.extend(["", "Evidence", prompt.details])
        return "\n".join(lines)

    @staticmethod
    def _format_approval_details(details: str) -> str:
        """把机器审批 JSON 投影成人能逐项核对的完整视图。"""

        try:
            payload = json.loads(details)
        except (json.JSONDecodeError, TypeError):
            return details
        if not isinstance(payload, dict):
            return details

        arguments = payload.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        lines = [
            f"工具：{payload.get('tool') or '-'}",
            f"动作：{payload.get('action') or '-'}",
        ]
        command = str(payload.get("command") or "")
        if command:
            lines.append(f"命令：{command}")

        target_path = arguments.get("path")
        if target_path is not None:
            lines.extend(["", f"目标文件：{target_path}"])
        if "old" in arguments:
            lines.extend(["", "原内容（old）：", str(arguments["old"])])
        if "new" in arguments:
            lines.extend(["", "新内容（new）：", str(arguments["new"])])

        remaining_arguments = {
            key: value
            for key, value in arguments.items()
            if key not in {"path", "old", "new"}
        }
        if remaining_arguments:
            lines.extend(
                [
                    "",
                    "其他参数：",
                    json.dumps(
                        remaining_arguments,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                ]
            )

        workspace = str(payload.get("workspace") or "")
        if workspace:
            lines.extend(["", f"隔离工作区：{workspace}"])
        fingerprint = payload.get("fingerprint")
        if fingerprint:
            lines.extend(
                [
                    "",
                    "审批时目标指纹：",
                    json.dumps(
                        fingerprint,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                ]
            )
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
