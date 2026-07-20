import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.cli.inspection import render_inspection
from agent_forge.observability.api import write_run_manifest


class UnifiedInspectionTest(unittest.TestCase):
    def test_run_artifact_and_symbol_share_one_read_only_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            trace_path = run_dir / "trace.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "run_id": "run-1",
                        "events": [
                            {"event_type": "llm_call"},
                            {"event_type": "tool_observation"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "patch.diff").write_text("+value = 2\n", encoding="utf-8")
            write_run_manifest(
                run_dir,
                run_id="run-1",
                task="update value",
                status="completed",
                stop_reason="final_answer",
            )

            story = render_inspection(str(run_dir))
            artifact = render_inspection(str(run_dir / "patch.diff"))

        symbol = render_inspection("AgentLoop.run")
        self.assertIn("Run Story", story)
        self.assertIn("candidate", story)
        self.assertIn("Artifact Lineage", artifact)
        self.assertIn("does not prove", artifact)
        self.assertIn("Code Compass", symbol)
        self.assertIn("规范上游", symbol)


if __name__ == "__main__":
    unittest.main()
