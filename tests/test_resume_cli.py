import tempfile
import unittest
from pathlib import Path

from agent_forge.cli.resume import write_resume_link
from agent_forge.runtime.api import latest_checkpoint_path
from agent_forge.runtime.application.operator_control import checkpoint_resume_workspace
from agent_forge.runtime.adapters import JsonTaskStateRepository
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus


class ResumeCliTest(unittest.TestCase):
    def test_resume_uses_requested_workspace_after_temporary_worktree_cleanup(self):
        checkpoint = TaskCheckpoint(
            run_id="run-1",
            task="continue",
            workspace="/tmp/removed-worktree",
            status="blocked",
            metadata={
                "execution_environment": {
                    "mode": "worktree",
                    "requested_workspace": "/tmp/original-repo",
                }
            },
        )

        self.assertEqual(checkpoint_resume_workspace(checkpoint), "/tmp/original-repo")

    def test_latest_checkpoint_path_returns_newest_checkpoint_under_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            store = JsonTaskStateRepository(run_dir / "task_state")
            first = store.start("first", "old task", tmp, "CodingAgent")
            second = store.start("second", "new task", tmp, "CodingAgent")
            store.update(first, status=TaskRunStatus.BLOCKED.value, updated_at=1)
            store.update(second, status=TaskRunStatus.WAITING_APPROVAL.value, updated_at=2)

            self.assertEqual(Path(latest_checkpoint_path(str(run_dir))), store.path_for("second"))

    def test_write_resume_link_adds_report_visible_resume_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "new-run"
            source_run = Path(tmp) / "old-run"
            run_dir.mkdir()
            source_run.mkdir()
            report = run_dir / "usage_report.md"
            report.write_text("# Usage Report\n\nExisting evidence.\n", encoding="utf-8")

            checkpoint_path = source_run / "task_state" / "previous.json"
            checkpoint_path.parent.mkdir()
            checkpoint_path.write_text("{}", encoding="utf-8")

            write_resume_link(
                run_dir,
                resumed_from_run_dir=source_run,
                resume_state=checkpoint_path,
                previous_run_id="run-old",
            )

            link = (run_dir / "resume_link.json").read_text(encoding="utf-8")
            chain = (run_dir / "resume_chain.md").read_text(encoding="utf-8")
            report_text = report.read_text(encoding="utf-8")

            self.assertIn("run-old", link)
            self.assertIn("# Resume Chain", chain)
            self.assertIn(str(checkpoint_path), chain)
            self.assertIn("## Resume Chain", report_text)
            self.assertIn(str(source_run), report_text)


if __name__ == "__main__":
    unittest.main()
