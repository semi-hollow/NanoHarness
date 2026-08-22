import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import write_run_manifest
from apps.operator_console.adapters import JsonTaskSessionCatalog
from apps.operator_console.application import TaskSessionLibrary
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus


class TaskSessionLibraryTest(unittest.TestCase):
    def test_create_rename_pin_and_archive_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = JsonTaskSessionCatalog(Path(tmp) / "sessions")
            library = TaskSessionLibrary(catalog)

            session = library.create(
                task="Repair settlement reconciliation and verify focused tests",
                workspace=tmp,
            )
            renamed = library.rename(session.session_id, "结算幂等修复")
            pinned = library.toggle_pinned(session.session_id)

            self.assertEqual(renamed.title, "结算幂等修复")
            self.assertTrue(pinned.pinned)
            self.assertEqual(library.list_active()[0].session_id, session.session_id)

            library.set_archived(session.session_id)
            self.assertEqual(library.list_active(), [])
            self.assertTrue(catalog.get(session.session_id).archived)

    def test_existing_continuation_runs_are_grouped_by_human_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            catalog = JsonTaskSessionCatalog(root / "sessions")
            library = TaskSessionLibrary(catalog)
            first_checkpoint = self._write_run(
                runs_root / "run-first",
                run_id="run-id-1",
                thread_id="thread-stable",
                task="Repair pricing",
                status=TaskRunStatus.PAUSED.value,
                current_step=3,
            )
            self._write_run(
                runs_root / "run-second",
                run_id="run-id-2",
                thread_id="thread-stable",
                task="Continue pricing repair",
                status=TaskRunStatus.COMPLETED.value,
                current_step=6,
                resume_state=str(first_checkpoint),
            )

            imported = library.import_existing_runs(runs_root)
            sessions = library.list_active()

            self.assertEqual(imported, 2)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].human_thread_id, "thread-stable")
            self.assertEqual(len(sessions[0].runs), 2)
            self.assertEqual(sessions[0].latest_run.status, "completed")
            self.assertEqual(sessions[0].latest_run.parent_run_id, "run-id-1")

            self.assertEqual(library.import_existing_runs(runs_root), 0)
            self.assertEqual(len(library.list_active()[0].runs), 2)

    def test_legacy_runs_without_thread_id_are_grouped_by_normalized_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            library = TaskSessionLibrary(JsonTaskSessionCatalog(root / "sessions"))
            self._write_run(
                runs_root / "run-first",
                run_id="legacy-1",
                thread_id="",
                task="Repair settlement service",
                status=TaskRunStatus.BLOCKED.value,
                current_step=4,
            )
            self._write_run(
                runs_root / "run-second",
                run_id="legacy-2",
                thread_id="",
                task="  Repair   settlement service  ",
                status=TaskRunStatus.COMPLETED.value,
                current_step=7,
            )

            self.assertEqual(library.import_existing_runs(runs_root), 2)
            sessions = library.list_active()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(len(sessions[0].runs), 2)
            self.assertTrue(sessions[0].human_thread_id.startswith("legacy-task-"))

    @staticmethod
    def _write_run(
        run_dir: Path,
        *,
        run_id: str,
        thread_id: str,
        task: str,
        status: str,
        current_step: int,
        resume_state: str = "",
    ) -> Path:
        state_dir = run_dir / "task_state"
        state_dir.mkdir(parents=True)
        checkpoint = TaskCheckpoint(
            run_id=run_id,
            task=task,
            workspace=str(run_dir.parent / "workspace"),
            status=status,
            current_step=current_step,
            metadata={"human_thread_id": thread_id},
        )
        checkpoint_path = state_dir / f"{run_id}.json"
        checkpoint_path.write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )
        (run_dir / "run_request.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "request": {
                        "task": task,
                        "workspace": checkpoint.workspace,
                        "human_thread_id": thread_id,
                        "resume_state": resume_state,
                    },
                    "config": {},
                }
            ),
            encoding="utf-8",
        )
        write_run_manifest(
            run_dir,
            run_id=run_id,
            task=task,
            status=status,
            stop_reason="",
        )
        return checkpoint_path


if __name__ == "__main__":
    unittest.main()
