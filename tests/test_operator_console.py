import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.control import RunController
from agent_forge.cli.parser import build_parser
from agent_forge.harness import Harness, HarnessConfig, HarnessExtensions, RunRequest
from agent_forge.observability.adapters.streaming import EventStreamPolicy
from agent_forge.operator_console.events import (
    RuntimeEventBuffer,
    render_event,
    should_render_event,
)
from agent_forge.operator_console.session import OperatorSession
from agent_forge.runtime.adapters.approval_json import JsonApprovalRepository
from agent_forge.runtime.adapters.human_input_json import JsonHumanInputRepository
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.llm_client import AgentResponse


class AskThenFinishModel:
    last_usage = None

    def chat(self, messages, tools):
        visible_context = "\n".join(
            str(getattr(message, "content", "")) for message in messages
        )
        if "Python 3.11" in visible_context:
            return AgentResponse("PASS\ncontinued with operator input", [])
        return AgentResponse(
            None,
            [
                ToolCall(
                    "ask-version",
                    "ask_human",
                    {"question": "Which Python version should be used?"},
                )
            ],
        )


class PatchUntilAppliedModel:
    last_usage = None

    def __init__(self, target: Path) -> None:
        self.target = target

    def chat(self, messages, tools):
        if self.target.read_text(encoding="utf-8") == "value = 1\n":
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "patch-target",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    )
                ],
            )
        return AgentResponse("PASS\napproved patch applied", [])


class FullConsoleStoryModel:
    """只根据模型可见上下文和真实文件状态推进完整 Console 故事。"""

    last_usage = None

    def __init__(self, target: Path) -> None:
        self.target = target

    def chat(self, messages, tools):
        visible_context = "\n".join(
            str(getattr(message, "content", "")) for message in messages
        )
        if "Python 3.11" not in visible_context:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "ask-version",
                        "ask_human",
                        {
                            "question": "Which Python version should be used?",
                            "choices": ["Python 3.11", "Python 3.12"],
                        },
                    )
                ],
            )
        if self.target.read_text(encoding="utf-8") == "value = 1\n":
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "patch-target",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    )
                ],
            )
        if "exit_code=0" not in visible_context:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "validate-target",
                        "diagnostics",
                        {"kind": "pytest", "target": "test_target.py"},
                    )
                ],
            )
        return AgentResponse(
            "PASS\nHITL, approval, patch and diagnostics completed", []
        )


class OperatorConsoleTest(unittest.TestCase):
    def test_human_answer_is_persisted_and_console_continues_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session, events = self._session(
                root,
                model=AskThenFinishModel(),
                enabled_tools=("ask_human",),
            )

            waiting = session.start()

            self.assertEqual(waiting.status, TaskRunStatus.WAITING_HUMAN)
            prompt = session.pending_prompt()
            self.assertIsNotNone(prompt)
            self.assertEqual(prompt.kind, "human_input")
            self.assertIn("Python version", prompt.body)

            completed = session.answer_and_resume("Python 3.11")

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertIn("continued with operator input", completed.final_answer)
            self.assertIn("Python 3.11", completed.checkpoint.task)
            self.assertIn(
                "do not ask the same question again",
                completed.checkpoint.task,
            )
            stored = session.human_inputs.get(prompt.key)
            self.assertEqual(stored.status, "responded")
            self.assertEqual(stored.answer, "Python 3.11")
            names = [event.name for event in events.drain(limit=500)]
            self.assertIn("human.required", names)
            self.assertIn("checkpoint.saved", names)

    def test_approval_stops_before_side_effect_then_resumes_real_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("value = 1\n", encoding="utf-8")
            session, _ = self._session(
                root,
                model=PatchUntilAppliedModel(target),
                enabled_tools=("replace_text",),
                approval_mode="on-write",
                auto_approve_writes=False,
            )

            waiting = session.start()

            self.assertEqual(waiting.status, TaskRunStatus.WAITING_APPROVAL)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            prompt = session.pending_prompt()
            self.assertIsNotNone(prompt)
            self.assertEqual(prompt.kind, "approval")
            self.assertIn('"tool": "replace_text"', prompt.details)

            completed = session.decide_and_resume("approved")

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            self.assertEqual(session.approvals.get(prompt.key).status, "approved")

    def test_attach_latest_restores_operator_boundary_without_running_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session, _ = self._session(
                root,
                model=AskThenFinishModel(),
                enabled_tools=("ask_human",),
            )
            waiting = session.start()
            pointer = root / ".agent_forge" / "latest" / "run.txt"
            self.assertTrue(pointer.is_file())

            attached, _ = self._session(
                root,
                model=AskThenFinishModel(),
                enabled_tools=("ask_human",),
            )
            checkpoint = attached.attach_latest(root)

            self.assertEqual(checkpoint.status, TaskRunStatus.WAITING_HUMAN.value)
            self.assertEqual(attached.artifact_dir, waiting.artifact_dir.resolve())
            self.assertEqual(attached.pending_prompt().kind, "human_input")

    def test_full_console_story_asks_once_then_approves_patches_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("value = 1\n", encoding="utf-8")
            (root / "test_target.py").write_text(
                "from target import value\n\n\ndef test_value():\n"
                "    assert value == 2\n",
                encoding="utf-8",
            )
            session, events = self._session(
                root,
                model=FullConsoleStoryModel(target),
                enabled_tools=("ask_human", "replace_text", "diagnostics"),
                approval_mode="on-write",
                auto_approve_writes=False,
            )

            waiting_human = session.start()
            self.assertEqual(waiting_human.status, TaskRunStatus.WAITING_HUMAN)
            human_prompt = session.require_prompt("human_input")

            waiting_approval = session.answer_and_resume("Python 3.11")
            self.assertEqual(
                waiting_approval.status,
                TaskRunStatus.WAITING_APPROVAL,
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            self.assertIn("Python 3.11", waiting_approval.checkpoint.task)

            completed = session.decide_and_resume("approved")

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            self.assertIn("diagnostics completed", completed.final_answer)
            self.assertEqual(
                len(list((root / ".agent_forge" / "human_input").glob("*.json"))),
                1,
                "The approval continuation must not create a second human question",
            )
            self.assertEqual(
                session.human_inputs.get(human_prompt.key).status,
                "responded",
            )
            event_names = [event.name for event in events.drain(limit=1_000)]
            self.assertIn("human.required", event_names)
            self.assertIn("approval.updated", event_names)
            self.assertIn("tool.completed", event_names)

    def test_event_rendering_keeps_timeline_compact(self):
        from agent_forge.observability.domain.live_event import RuntimeEvent

        rendered = render_event(
            RuntimeEvent(
                name="tool.proposed",
                run_id="run-1",
                sequence=7,
                step=2,
                agent_name="CodingAgent",
                payload={"tool_name": "read_file", "arguments": {"path": "a.py"}},
            )
        )

        self.assertIn("Step 2", rendered)
        self.assertIn("工具提议", rendered)
        self.assertIn("a.py", rendered)
        self.assertEqual(rendered.count("\n"), 0)

    def test_run_level_event_does_not_look_like_step_zero(self):
        from agent_forge.observability.domain.live_event import RuntimeEvent

        rendered = render_event(
            RuntimeEvent(
                name="run.published",
                run_id="run-1",
                sequence=53,
                step=0,
                agent_name="Runtime",
            )
        )

        self.assertIn("Run", rendered)
        self.assertIn("Evidence 已落盘", rendered)
        self.assertNotIn("Step 0", rendered)
        self.assertNotIn("Turn 0", rendered)

    def test_core_timeline_hides_hooks_and_running_checkpoints(self):
        from agent_forge.observability.domain.live_event import RuntimeEvent

        hook_event = RuntimeEvent(
            name="runtime.stop_hooks",
            run_id="run-1",
            sequence=51,
            step=2,
            agent_name="Runtime",
            payload={"hook_decisions": [{"metadata": "x" * 2_000}]},
        )
        running_checkpoint = RuntimeEvent(
            name="checkpoint.saved",
            run_id="run-1",
            sequence=50,
            step=2,
            agent_name="Runtime",
            payload={"status": "running"},
        )
        waiting_checkpoint = RuntimeEvent(
            name="checkpoint.saved",
            run_id="run-1",
            sequence=52,
            step=2,
            agent_name="Runtime",
            payload={
                "status": "waiting_approval",
                "messages_count": 4,
                "observations_count": 2,
            },
        )

        self.assertFalse(should_render_event(hook_event))
        self.assertFalse(should_render_event(running_checkpoint))
        self.assertTrue(should_render_event(waiting_checkpoint))
        self.assertTrue(
            should_render_event(
                hook_event,
                include_infrastructure=True,
            )
        )
        self.assertLess(
            len(
                render_event(
                    hook_event,
                    include_infrastructure=True,
                )
            ),
            300,
        )

    def test_textual_console_mounts_with_primary_controls(self):
        from agent_forge.operator_console.app import OperatorConsoleApp
        from textual.widgets import Button, RichLog, TextArea

        async def exercise() -> None:
            app = OperatorConsoleApp(build_parser().parse_args(["console"]))
            async with app.run_test(size=(140, 44)) as pilot:
                self.assertIsNotNone(app.query_one("#task", TextArea))
                self.assertIsNotNone(app.query_one("#timeline", RichLog))
                self.assertEqual(app.query_one("#start", Button).label.plain, "运行")
                self.assertEqual(
                    app.query_one("#timeline-mode", Button).label.plain,
                    "显示底层事件",
                )
                self.assertFalse(app.query_one("#approve", Button).display)
                await pilot.click("#timeline-mode")
                self.assertEqual(
                    app.query_one("#timeline-mode", Button).label.plain,
                    "只看主流程",
                )

        asyncio.run(exercise())

    def test_textual_console_drives_real_hitl_continuation(self):
        from agent_forge.operator_console.api import OperatorSessionBundle
        from agent_forge.operator_console.app import OperatorConsoleApp
        from textual.containers import Vertical
        from textual.widgets import Input, Static

        async def exercise(root: Path) -> None:
            session, events = self._session(
                root,
                model=AskThenFinishModel(),
                enabled_tools=("ask_human",),
            )
            bundle = OperatorSessionBundle(
                session=session,
                events=events,
                request=session.request,
            )
            args = build_parser().parse_args(
                ["console", "exercise HITL", "--workspace", str(root)]
            )
            with patch(
                "agent_forge.operator_console.app.build_operator_session",
                return_value=bundle,
            ):
                app = OperatorConsoleApp(args)
                async with app.run_test(size=(80, 25)) as pilot:
                    await pilot.click("#start")
                    for _ in range(100):
                        await pilot.pause(0.02)
                        if (
                            session.checkpoint is not None
                            and session.checkpoint.status
                            == TaskRunStatus.WAITING_HUMAN.value
                        ):
                            break
                    self.assertEqual(
                        session.checkpoint.status,
                        TaskRunStatus.WAITING_HUMAN.value,
                    )
                    self.assertFalse(app.query_one("#launch", Vertical).display)
                    operator_input = app.query_one("#operator-input", Input)
                    self.assertFalse(operator_input.disabled)
                    for selector in (
                        "#status",
                        "#prompt",
                        "#operator-input",
                        "#operator-actions",
                        "#send",
                        "#pause",
                        "#cancel",
                    ):
                        region = app.query_one(selector).region
                        self.assertGreater(region.height, 0)
                        self.assertLessEqual(
                            region.bottom,
                            app.screen.region.bottom,
                            selector,
                        )
                        self.assertLessEqual(
                            region.right,
                            app.screen.region.right,
                            selector,
                        )
                    self.assertIn(
                        "Python version",
                        str(app.query_one("#prompt", Static).render()),
                    )

                    operator_input.value = "Python 3.11"
                    await pilot.click("#send")
                    for _ in range(100):
                        await pilot.pause(0.02)
                        if (
                            session.checkpoint is not None
                            and session.checkpoint.status
                            == TaskRunStatus.COMPLETED.value
                        ):
                            break
                    self.assertEqual(
                        session.checkpoint.status,
                        TaskRunStatus.COMPLETED.value,
                    )
                    self.assertIn(
                        "continued with operator input",
                        str(app.query_one("#prompt", Static).render()),
                    )

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    @staticmethod
    def _session(
        root: Path,
        *,
        model,
        enabled_tools: tuple[str, ...],
        approval_mode: str = "trusted",
        auto_approve_writes: bool = True,
    ) -> tuple[OperatorSession, RuntimeEventBuffer]:
        approvals = JsonApprovalRepository(root / ".agent_forge" / "approvals")
        human_inputs = JsonHumanInputRepository(root / ".agent_forge" / "human_input")
        controller = RunController()
        events = RuntimeEventBuffer()
        harness = Harness(
            model=model,
            config=HarnessConfig(
                workspace=str(root),
                output_root=str(root / "runs"),
                max_steps=5,
                enabled_tools=enabled_tools,
                tool_routing_mode="all",
                skill_mode="none",
                memory_recall_limit=0,
                approval_mode=approval_mode,
                auto_approve_writes=auto_approve_writes,
            ),
            extensions=HarnessExtensions(
                event_listeners=(events,),
                event_stream_policy=EventStreamPolicy(include_sensitive_data=True),
                approval_repository=approvals,
                human_input_repository=human_inputs,
                run_control=controller,
            ),
        )
        session = OperatorSession(
            harness=harness,
            request=RunRequest("complete the operator-console test task"),
            controller=controller,
            approvals=approvals,
            human_inputs=human_inputs,
        )
        return session, events


if __name__ == "__main__":
    unittest.main()
