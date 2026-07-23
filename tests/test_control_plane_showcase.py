import json
import tempfile
import unittest
from agent_forge.showcase import run_governed_demo
from agent_forge.showcase.control_plane import (
    _continue_control_plane_demo,
    _start_control_plane_demo,
)


class ControlPlaneShowcaseTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
