import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_multi_agent_v1_smoke import run_smoke


class MultiAgentV1SmokeTest(unittest.TestCase):
    def test_natural_task_runs_real_planner_agentloops_worktrees_and_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "sanitized.json"
            result = run_smoke(
                raw_root=root / "raw",
                summary_path=summary_path,
            )

            self.assertTrue(all(result["assertions"].values()))
            self.assertFalse(result["real_model_performance_evaluated"])
            self.assertEqual(result["benchmark_claim"], "none")
            serialized = summary_path.read_text(encoding="utf-8")
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("Operator steer", serialized)
            self.assertEqual(json.loads(serialized), result)


if __name__ == "__main__":
    unittest.main()
