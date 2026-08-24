import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from apps.cli.parser import build_parser
from apps.cli.resume import resume_repository_task, write_resume_link
from agent_forge.observability.api import read_run_manifest, write_run_manifest
from agent_forge.runtime.api import latest_checkpoint_path
from agent_forge.runtime.adapters import JsonTaskStateRepository
from agent_forge.runtime.domain.task import (
    PendingExecutionPointer,
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)


class ResumeCliTest(unittest.TestCase):
    @staticmethod
    def _checkpoint(workspace: str, status: str = "paused") -> TaskCheckpoint:
        return TaskCheckpoint(
            run_id="run-old",
            thread_id="thread-1",
            turn_id="turn-1",
            workspace=workspace,
            execution_workspace=workspace,
            execution_mode="local",
            status=status,
            pending_execution=(
                PendingExecutionPointer(
                    assistant_item_id="assistant-1",
                    pending_operation_key="operation-1",
                )
                if status == "waiting_approval"
                else None
            ),
            metadata=(
                {"human_input_request_id": "request-1"}
                if status == "waiting_human"
                else {}
            ),
        )

    @staticmethod
    def _mock_harness() -> mock.MagicMock:
        harness = mock.MagicMock()
        harness.resume.return_value = SimpleNamespace(
            artifact_dir=Path("/tmp/new-run")
        )
        return harness

    def test_checkpoint_separates_requested_and_execution_workspace(self) -> None:
        checkpoint = TaskCheckpoint(
            run_id="run-1",
            thread_id="thread-1",
            turn_id="turn-1",
            workspace="/tmp/original-repo",
            execution_workspace="/tmp/worktrees/run-1",
            execution_mode="worktree",
            status="paused",
        )

        self.assertEqual(checkpoint.workspace, "/tmp/original-repo")
        self.assertEqual(checkpoint.execution_workspace, "/tmp/worktrees/run-1")

    def test_latest_checkpoint_path_returns_newest_checkpoint_under_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            store = JsonTaskStateRepository(run_dir / "task_state")
            first = store.start(
                TaskStartRequest(
                    run_id="first",
                    thread_id="thread-1",
                    turn_id="turn-1",
                    workspace=tmp,
                    execution_workspace=tmp,
                    execution_mode="local",
                    agent_name="CodingAgent",
                )
            )
            second = store.start(
                TaskStartRequest(
                    run_id="second",
                    thread_id="thread-1",
                    turn_id="turn-1",
                    workspace=tmp,
                    execution_workspace=tmp,
                    execution_mode="local",
                    agent_name="CodingAgent",
                )
            )
            store.update(
                first,
                TaskCheckpointUpdate(status=TaskRunStatus.BLOCKED, updated_at=1),
            )
            store.update(
                second,
                TaskCheckpointUpdate(
                    status=TaskRunStatus.WAITING_APPROVAL,
                    updated_at=2,
                ),
            )

            self.assertEqual(
                Path(latest_checkpoint_path(str(run_dir))),
                store.path_for("second"),
            )

    def test_write_resume_link_adds_report_visible_resume_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "new-run"
            source_run = Path(tmp) / "old-run"
            run_dir.mkdir()
            source_run.mkdir()
            report = run_dir / "usage_report.md"
            report.write_text("# Usage Report\n\nExisting evidence.\n", encoding="utf-8")
            write_run_manifest(
                run_dir,
                run_id="run-new",
                task="continue",
                status="completed",
                stop_reason="final_answer",
            )
            checkpoint_path = source_run / "task_state" / "previous.json"
            checkpoint_path.parent.mkdir()
            checkpoint_path.write_text("{}", encoding="utf-8")

            write_resume_link(
                run_dir,
                resumed_from_run_dir=source_run,
                resume_state=checkpoint_path,
                previous_run_id="run-old",
            )

            self.assertIn(
                "run-old",
                (run_dir / "resume_link.json").read_text(encoding="utf-8"),
            )
            kinds = {
                artifact.kind
                for artifact in read_run_manifest(
                    run_dir / "run_manifest.json"
                ).artifacts
            }
            self.assertIn("resume_link", kinds)
            self.assertIn("resume_chain_report", kinds)

    def test_resume_inherits_config_and_calls_harness_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_run = Path(tmp) / "old-run"
            workspace = Path(tmp) / "repository"
            source_run.mkdir()
            workspace.mkdir()
            (source_run / "resolved_config.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "values": {
                            "provider": "openai",
                            "model": "source-model",
                            "max_steps": 4,
                            "native_tool_calling": True,
                            "approval_mode": "on-risk",
                            "execution_mode": "local",
                            "enabled_tools": ["read_file"],
                            "agent_mode": "single",
                            "runtime_instructions_configured": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                ["resume", str(source_run), "--max-steps", "9"]
            )
            checkpoint_path = source_run / "task_state" / "checkpoint.json"
            harness = self._mock_harness()
            with mock.patch(
                "apps.cli.resume.latest_checkpoint_path",
                return_value=str(checkpoint_path),
            ), mock.patch(
                "apps.cli.resume.load_task_checkpoint",
                return_value=self._checkpoint(str(workspace)),
            ), mock.patch(
                "apps.cli.resume.build_single_harness",
                return_value=harness,
            ) as build, mock.patch("apps.cli.resume.write_resume_link"):
                resume_repository_task(args)

            forwarded = build.call_args.args[0]
            self.assertEqual(forwarded.model, "source-model")
            self.assertEqual(forwarded.max_steps, 9)
            self.assertEqual(forwarded.thread_id, "thread-1")
            self.assertEqual(Path(forwarded.workspace), workspace)
            harness.resume.assert_called_once_with(str(checkpoint_path))

    def test_resume_rejects_task_override(self) -> None:
        args = build_parser().parse_args(
            ["resume", "/tmp/missing-run", "--task", "different task"]
        )
        with mock.patch(
            "apps.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "apps.cli.resume.load_task_checkpoint",
            return_value=self._checkpoint("/tmp/repository"),
        ):
            with self.assertRaisesRegex(SystemExit, "cannot override Turn.root_task"):
                resume_repository_task(args)

    def test_resume_answers_pending_human_request_before_harness(self) -> None:
        args = build_parser().parse_args(
            ["resume", "/tmp/missing-run", "--answer", "Python 3.11"]
        )
        pending = SimpleNamespace(request_id="request-1")
        harness = self._mock_harness()
        with mock.patch(
            "apps.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "apps.cli.resume.load_task_checkpoint",
            return_value=self._checkpoint("/tmp/repository", "waiting_human"),
        ), mock.patch(
            "apps.cli.resume.list_pending_human_inputs",
            return_value=[pending],
        ), mock.patch(
            "apps.cli.resume.respond_to_human_input"
        ) as respond, mock.patch(
            "apps.cli.resume.build_single_harness",
            return_value=harness,
        ), mock.patch("apps.cli.resume.write_resume_link"):
            resume_repository_task(args)

        command = respond.call_args.args[0]
        self.assertEqual(command.request_id, "request-1")
        self.assertEqual(command.answer, "Python 3.11")
        harness.resume.assert_called_once_with("/tmp/checkpoint.json")

    def test_resume_requires_explicit_approval_decision(self) -> None:
        args = build_parser().parse_args(["resume", "/tmp/missing-run"])
        pending = SimpleNamespace(operation_key="operation-1")
        with mock.patch(
            "apps.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "apps.cli.resume.load_task_checkpoint",
            return_value=self._checkpoint("/tmp/repository", "waiting_approval"),
        ), mock.patch(
            "apps.cli.resume.list_pending_approvals",
            return_value=[pending],
        ):
            with self.assertRaisesRegex(SystemExit, "--decision approved"):
                resume_repository_task(args)

    def test_resume_rejects_human_request_from_another_checkpoint(self) -> None:
        args = build_parser().parse_args(
            [
                "resume",
                "/tmp/missing-run",
                "--answer",
                "Python 3.11",
                "--request-id",
                "request-other",
            ]
        )
        with mock.patch(
            "apps.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "apps.cli.resume.load_task_checkpoint",
            return_value=self._checkpoint("/tmp/repository", "waiting_human"),
        ), mock.patch("apps.cli.resume.respond_to_human_input") as respond:
            with self.assertRaisesRegex(SystemExit, "does not match resume checkpoint"):
                resume_repository_task(args)

        respond.assert_not_called()

    def test_resume_rejects_approval_from_another_checkpoint(self) -> None:
        args = build_parser().parse_args(
            [
                "resume",
                "/tmp/missing-run",
                "--decision",
                "approved",
                "--operation-key",
                "operation-other",
            ]
        )
        with mock.patch(
            "apps.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "apps.cli.resume.load_task_checkpoint",
            return_value=self._checkpoint("/tmp/repository", "waiting_approval"),
        ), mock.patch("apps.cli.resume.decide_approval") as decide:
            with self.assertRaisesRegex(SystemExit, "does not match resume checkpoint"):
                resume_repository_task(args)

        decide.assert_not_called()


if __name__ == "__main__":
    unittest.main()
