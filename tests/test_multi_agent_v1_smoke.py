import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_multi_agent_v1_smoke import run_smoke


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKED_MECHANISM_EVIDENCE = (
    PROJECT_ROOT
    / "benchmarks"
    / "experiments"
    / "multi-agent-v1"
    / "mechanism-evidence.json"
)


class MultiAgentV1SmokeTest(unittest.TestCase):
    def test_natural_task_runs_real_planner_agentloops_worktrees_and_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw"
            summary_path = root / "sanitized.json"
            result = run_smoke(
                raw_root=raw_root,
                summary_path=summary_path,
            )

            self.assertTrue(all(result["assertions"].values()))
            self.assertFalse(result["real_model_performance_evaluated"])
            self.assertEqual(result["benchmark_claim"], "none")
            serialized = summary_path.read_text(encoding="utf-8")
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("Operator steer", serialized)
            self.assertEqual(json.loads(serialized), result)
            self.assertEqual(
                json.loads(CHECKED_MECHANISM_EVIDENCE.read_text(encoding="utf-8")),
                result,
                "checked mechanism evidence must equal a fresh deterministic smoke",
            )

            # Worker 与 Finalizer 都有自己的 durable execution conversation；
            # runtime_plan 只是 provider user-role transport，不获得 human authority。
            run_root = raw_root / "deterministic-smoke-v1"
            worker_threads = sorted(
                run_root.glob("fanout/workers/*/attempt-*/threads/*/thread.json")
            )
            finalizer_threads = sorted(
                run_root.glob("fanout/finalizer/threads/*/thread.json")
            )
            self.assertEqual(len(worker_threads), 2)
            self.assertEqual(len(finalizer_threads), 1)
            for thread_path in [*worker_threads, *finalizer_threads]:
                metadata = json.loads(thread_path.read_text(encoding="utf-8"))
                self.assertIn(metadata["thread_kind"], {"worker", "finalizer"})
                conversation_path = thread_path.with_name("conversation.jsonl")
                items = [
                    json.loads(line)
                    for line in conversation_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertGreaterEqual(len(items), 2)
                self.assertEqual(items[0]["origin"], "runtime_plan")
                self.assertFalse(items[0]["human_authority"])


if __name__ == "__main__":
    unittest.main()
