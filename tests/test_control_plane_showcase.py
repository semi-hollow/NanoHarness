import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path

from textual.widgets import Input

from apps.showcase import run_governed_demo
from apps.showcase.console import (
    GovernedShowcaseConsoleApp,
    _readable_durable_paths,
)
from apps.showcase.control_plane import (
    GOVERNED_PLACEHOLDER,
    GovernedShowcaseController,
    _continue_control_plane_demo,
    _start_control_plane_demo,
)

OPERATOR_REQUEST = "将审批后的人工要求写入运行产物"


class ControlPlaneShowcaseTest(unittest.TestCase):
    def test_project_owned_showcase_does_not_nest_agent_forge_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = GovernedShowcaseController(
                output_root=root / ".agent_forge" / "runs" / "showcases"
            )

            waiting_human = controller.start()

            self.assertRegex(
                waiting_human.run_dir.name,
                re.compile(
                    r"^lab1-governed-change-control__\d{4}-\d{2}-\d{2}_"
                    r"\d{2}-\d{2}-\d{2}__[a-z0-9]{7}$"
                ),
            )

            self.assertFalse((waiting_human.workspace / ".agent_forge").exists())
            self.assertTrue(
                (root / ".agent_forge" / "internal" / "state" / "approvals").is_dir()
            )
            self.assertTrue((waiting_human.run_dir / "human_input").is_dir())
            pointer = root / ".agent_forge" / "internal" / "index" / "run.txt"
            self.assertEqual(
                Path(pointer.read_text(encoding="utf-8")).resolve(),
                waiting_human.artifact_dir.resolve(),
            )

    def test_governed_controller_requires_two_explicit_human_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GovernedShowcaseController(output_root=tmp)

            waiting_human = controller.start()
            target = waiting_human.workspace / "operator_request.txt"
            self.assertEqual(waiting_human.status, "waiting_human")
            self.assertEqual(target.read_text(encoding="utf-8"), f"{GOVERNED_PLACEHOLDER}\n")

            recorded_answer = controller.record_answer(OPERATOR_REQUEST)
            self.assertEqual(recorded_answer.action, "human_input_recorded")
            self.assertEqual(recorded_answer.status, "waiting_human")
            self.assertTrue(
                all(path.exists() for path in recorded_answer.durable_paths)
            )

            waiting_approval = controller.resume()
            self.assertEqual(waiting_approval.status, "waiting_approval")
            self.assertTrue(waiting_approval.operation_key)
            self.assertEqual(target.read_text(encoding="utf-8"), f"{GOVERNED_PLACEHOLDER}\n")

            recorded_approval = controller.record_decision("approved")
            self.assertEqual(recorded_approval.action, "approval_recorded")
            self.assertEqual(recorded_approval.status, "waiting_approval")
            self.assertTrue(
                all(path.exists() for path in recorded_approval.durable_paths)
            )

            completed = controller.resume()
            self.assertEqual(completed.status, "completed")
            self.assertEqual(target.read_text(encoding="utf-8"), f"{OPERATOR_REQUEST}\n")
            self.assertEqual(
                controller.state_sequence,
                ("waiting_human", "waiting_approval", "completed"),
            )
            human_trace = json.loads(
                waiting_approval.trace_path.read_text(encoding="utf-8")
            )
            self.assertIn(
                "human_input_response_loaded",
                [event["event_type"] for event in human_trace["events"]],
            )
            final_trace = json.loads(completed.trace_path.read_text(encoding="utf-8"))
            final_event_types = [
                event["event_type"] for event in final_trace["events"]
            ]
            self.assertIn("human_approval", final_event_types)
            self.assertIn("validation_evidence", final_event_types)
            self.assertTrue((completed.run_dir / "demo.md").is_file())
            approval_paths = _readable_durable_paths(recorded_approval)
            readable_paths = _readable_durable_paths(completed)
            self.assertIn("operation_ledger/<key>.json", approval_paths)
            self.assertNotIn(str(completed.run_dir), readable_paths)
            self.assertIn("current_phase/trace.json", readable_paths)

    def test_pause_and_cancel_are_persisted_at_runtime_safe_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GovernedShowcaseController(output_root=tmp)
            started = controller.start()
            controller.record_answer(OPERATOR_REQUEST)

            paused = controller.pause_at_safe_boundary()
            self.assertEqual(paused.status, "paused")
            paused_checkpoint = json.loads(
                paused.checkpoint_path.read_text(encoding="utf-8")
            )
            self.assertEqual(paused_checkpoint["status"], "paused")
            self.assertIn("pause", str(paused_checkpoint.get("stop_reason") or ""))

            cancelled = controller.cancel()
            self.assertEqual(cancelled.status, "cancelled")
            cancelled_checkpoint = json.loads(
                cancelled.checkpoint_path.read_text(encoding="utf-8")
            )
            self.assertEqual(cancelled_checkpoint["status"], "cancelled")
            self.assertEqual(
                (started.workspace / "operator_request.txt").read_text(encoding="utf-8"),
                f"{GOVERNED_PLACEHOLDER}\n",
            )

    def test_governed_rejection_keeps_workspace_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GovernedShowcaseController(output_root=tmp)
            controller.start()
            waiting_approval = controller.answer(OPERATOR_REQUEST)
            completed = controller.decide("rejected")

            self.assertEqual(waiting_approval.status, "waiting_approval")
            self.assertEqual(completed.status, "completed")
            self.assertEqual(
                (completed.workspace / "operator_request.txt").read_text(
                    encoding="utf-8"
                ),
                f"{GOVERNED_PLACEHOLDER}\n",
            )
            trace = json.loads(completed.trace_path.read_text(encoding="utf-8"))
            self.assertFalse(
                any(
                    event["event_type"] == "validation_evidence"
                    for event in trace["events"]
                )
            )

    def test_hitl_showcase_persists_answer_and_resumes_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = _start_control_plane_demo("hitl", output_root=tmp)

            self.assertEqual(started.status, "waiting_human")
            self.assertTrue(started.request_id)
            self.assertTrue(started.checkpoint_path.exists())
            report = (started.run_dir / "showcase.md").read_text(encoding="utf-8")
            self.assertIn("waiting_human", report)
            self.assertIn(started.request_id, report)

            completed = _continue_control_plane_demo(
                "hitl",
                started.run_dir,
                answer="Use the operator-provided request",
            )

            self.assertEqual(completed.status, "completed")
            trace = json.loads(completed.trace_path.read_text(encoding="utf-8"))
            event_types = [event["event_type"] for event in trace["events"]]
            self.assertIn("resume_state_loaded", event_types)
            self.assertIn("human_input_response_loaded", event_types)
            report = (completed.run_dir / "showcase.md").read_text(encoding="utf-8")
            self.assertIn("continuation 已完成", report)

    def test_approval_showcase_never_writes_before_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = _start_control_plane_demo("approval", output_root=tmp)
            target = started.workspace / "target.py"

            self.assertEqual(started.status, "waiting_approval")
            self.assertTrue(started.operation_key)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            report = (started.run_dir / "showcase.md").read_text(encoding="utf-8")
            self.assertIn("value = 1", report)

            completed = _continue_control_plane_demo(
                "approval",
                started.run_dir,
            )

            self.assertEqual(completed.status, "completed")
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            report = (completed.run_dir / "showcase.md").read_text(encoding="utf-8")
            self.assertIn("value = 2", report)
            trace = json.loads(completed.trace_path.read_text(encoding="utf-8"))
            approved = [
                event
                for event in trace["events"]
                if event["event_type"] == "human_approval"
                and event.get("observation") == "approved"
            ]
            self.assertEqual(len(approved), 1)
            validations = [
                event
                for event in trace["events"]
                if event["event_type"] == "validation_evidence"
            ]
            self.assertEqual(len(validations), 1)
            self.assertTrue(validations[0]["success"])

    def test_continuation_rejects_a_mismatched_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = _start_control_plane_demo("hitl", output_root=tmp)

            with self.assertRaisesRegex(ValueError, "scenario mismatch"):
                _continue_control_plane_demo("approval", started.run_dir)

    def test_one_command_demo_records_waiting_and_completion_claim_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_governed_demo("approval", output_root=tmp)

            self.assertEqual(result.waiting_status, "waiting_approval")
            self.assertEqual(result.completed_status, "completed")
            self.assertTrue((result.inspect_target / "run_manifest.json").exists())
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("running → waiting_approval", report)
            self.assertIn("does not prove", report)


class GovernedShowcaseConsoleTest(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_drive_the_two_runtime_barriers(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = GovernedShowcaseConsoleApp(output_root=Path(tmp))
            # PyCharm 的普通 Run 窗口常只有约 38 行；每个当前动作都必须留在
            # 可视区域内，不能只在 Textual 的虚拟布局中处于 display=True。
            async with app.run_test(size=(140, 38)) as pilot:
                self._assert_on_screen(app, "#start")
                self._assert_panels_fit(app)
                await pilot.click("#start")
                await self._wait_for(
                    lambda: app.query_one("#human-input-actions").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_human")
                self._assert_panels_fit(app)

                self._assert_on_screen(app, "#human-answer")
                self._assert_on_screen(app, "#save-human-answer")
                app.query_one("#human-answer", Input).value = OPERATOR_REQUEST
                await pilot.click("#save-human-answer")
                await self._wait_for(
                    lambda: app.query_one("#resume-actions").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_human")
                self.assertEqual(
                    app._controller.current.action,
                    "human_input_recorded",
                )
                self._assert_panels_fit(app)

                self._assert_on_screen(app, "#resume")
                await pilot.click("#resume")
                await self._wait_for(
                    lambda: app.query_one("#approval-actions").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_approval")
                self._assert_panels_fit(app)

                self._assert_on_screen(app, "#approve")
                await pilot.click("#approve")
                await self._wait_for(
                    lambda: app.query_one("#resume-actions").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_approval")
                self.assertEqual(
                    app._controller.current.action,
                    "approval_recorded",
                )
                self._assert_panels_fit(app)

                self._assert_on_screen(app, "#resume")
                await pilot.click("#resume")
                await self._wait_for(
                    lambda: app.query_one("#terminal-actions").display,
                )
                self.assertEqual(app._controller.current.status, "completed")
                self.assertEqual(
                    app._controller.state_sequence,
                    ("waiting_human", "waiting_approval", "completed"),
                )
                self._assert_panels_fit(app)

    def _assert_on_screen(self, app, selector: str) -> None:
        widget = app.query_one(selector)
        self.assertGreaterEqual(widget.region.y, app.screen.region.y)
        self.assertLessEqual(widget.region.bottom, app.screen.region.bottom)

    def _assert_panels_fit(self, app) -> None:
        for selector in ("#state", "#persistence", "#decision"):
            widget = app.query_one(selector)
            strips = widget.render().render_strips(
                widget,
                widget.content_size.width,
                None,
                widget.visual_style,
            )
            self.assertLessEqual(
                len(strips),
                widget.content_size.height,
                f"{selector} content is clipped in a 140x38 Run window",
            )

    async def _wait_for(self, predicate, *, timeout_seconds: float = 10.0):
        for _ in range(int(timeout_seconds * 20)):
            if predicate():
                return
            await asyncio.sleep(0.05)
        self.fail("Textual control state did not arrive before timeout")


if __name__ == "__main__":
    unittest.main()
