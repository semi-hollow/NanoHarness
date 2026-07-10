import tempfile
import unittest
from pathlib import Path

from agent_forge.forge_cli import latest_checkpoint_path
from agent_forge.runtime.task_state import TaskRunStatus, TaskStateStore


class ResumeCliTest(unittest.TestCase):
    def test_latest_checkpoint_path_returns_newest_checkpoint_under_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            store = TaskStateStore(run_dir / "task_state")
            first = store.start("first", "old task", tmp, "CodingAgent")
            second = store.start("second", "new task", tmp, "CodingAgent")
            store.update(first, status=TaskRunStatus.BLOCKED.value, updated_at=1)
            store.update(second, status=TaskRunStatus.WAITING_APPROVAL.value, updated_at=2)

            self.assertEqual(latest_checkpoint_path(run_dir), store.path_for("second"))


if __name__ == "__main__":
    unittest.main()
