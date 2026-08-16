import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path

from agent_forge.showcase import run_governed_demo
from agent_forge.showcase.console import GovernedShowcaseConsoleApp
from agent_forge.showcase.control_plane import (
    GOVERNED_CHOICES,
    GovernedShowcaseController,
    _continue_control_plane_demo,
    _start_control_plane_demo,
)


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
                    r"^lab1-python-compatibility-control__\d{4}-\d{2}-\d{2}_"
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
            target = waiting_human.workspace / "compatibility.py"
            self.assertEqual(waiting_human.status, "waiting_human")
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                'TARGET_RUNTIME = "unselected"\n',
            )

            recorded_answer = controller.record_answer(GOVERNED_CHOICES[0])
            self.assertEqual(recorded_answer.action, "human_input_recorded")
            self.assertEqual(recorded_answer.status, "waiting_human")
            self.assertTrue(
                all(path.exists() for path in recorded_answer.durable_paths)
            )

            waiting_approval = controller.resume()
            self.assertEqual(waiting_approval.status, "waiting_approval")
            self.assertTrue(waiting_approval.operation_key)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                'TARGET_RUNTIME = "unselected"\n',
            )

            recorded_approval = controller.record_decision("approved")
            self.assertEqual(recorded_approval.action, "approval_recorded")
            self.assertEqual(recorded_approval.status, "waiting_approval")
            self.assertTrue(
                all(path.exists() for path in recorded_approval.durable_paths)
            )

            completed = controller.resume()
            self.assertEqual(completed.status, "completed")
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                f'TARGET_RUNTIME = "{GOVERNED_CHOICES[0]}"\n',
            )
            self.assertEqual(
                controller.state_sequence,
                ("waiting_human", "waiting_approval", "completed"),
            )
            trace = json.loads(completed.trace_path.read_text(encoding="utf-8"))
            event_types = [event["event_type"] for event in trace["events"]]
            self.assertIn("human_input_response_loaded", event_types)
            self.assertIn("human_approval", event_types)
            self.assertIn("validation_evidence", event_types)
            self.assertTrue((completed.run_dir / "demo.md").is_file())

    def test_pause_and_cancel_are_persisted_at_runtime_safe_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GovernedShowcaseController(output_root=tmp)
            started = controller.start()
            controller.record_answer(GOVERNED_CHOICES[0])

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
                (started.workspace / "compatibility.py").read_text(encoding="utf-8"),
                'TARGET_RUNTIME = "unselected"\n',
            )

    def test_governed_rejection_keeps_workspace_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = GovernedShowcaseController(output_root=tmp)
            controller.start()
            waiting_approval = controller.answer(GOVERNED_CHOICES[1])
            completed = controller.decide("rejected")

            self.assertEqual(waiting_approval.status, "waiting_approval")
            self.assertEqual(completed.status, "completed")
            self.assertEqual(
                (completed.workspace / "compatibility.py").read_text(encoding="utf-8"),
                'TARGET_RUNTIME = "unselected"\n',
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
                answer="Python 3.11",
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
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.click("#start")
                await self._wait_for(
                    lambda: app.query_one("#choices").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_human")

                await pilot.click("#choice-lts")
                await self._wait_for(
                    lambda: app.query_one("#resume-actions").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_human")
                self.assertEqual(
                    app._controller.current.action,
                    "human_input_recorded",
                )

                await pilot.click("#resume")
                await self._wait_for(
                    lambda: app.query_one("#approval-actions").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_approval")

                await pilot.click("#approve")
                await self._wait_for(
                    lambda: app.query_one("#resume-actions").display,
                )
                self.assertEqual(app._controller.current.status, "waiting_approval")
                self.assertEqual(
                    app._controller.current.action,
                    "approval_recorded",
                )

                await pilot.click("#resume")
                await self._wait_for(
                    lambda: app.query_one("#terminal-actions").display,
                )
                self.assertEqual(app._controller.current.status, "completed")
                self.assertEqual(
                    app._controller.state_sequence,
                    ("waiting_human", "waiting_approval", "completed"),
                )

    async def _wait_for(self, predicate, *, timeout_seconds: float = 10.0):
        for _ in range(int(timeout_seconds * 20)):
            if predicate():
                return
            await asyncio.sleep(0.05)
        self.fail("Textual control state did not arrive before timeout")


if __name__ == "__main__":
    unittest.main()
