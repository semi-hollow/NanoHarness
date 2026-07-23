import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_forge.cli.parser import build_parser
from agent_forge.cli.resume import resume_repository_task, write_resume_link
from agent_forge.observability.api import read_run_manifest, write_run_manifest
from agent_forge.runtime.api import latest_checkpoint_path
from agent_forge.runtime.application.operator_control import ContinuationPlan
from agent_forge.runtime.application.operator_control import checkpoint_resume_workspace
from agent_forge.runtime.adapters import JsonTaskStateRepository
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)


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
            first = store.start(
                TaskStartRequest(
                    run_id="first",
                    task="old task",
                    workspace=tmp,
                    agent_name="CodingAgent",
                )
            )
            second = store.start(
                TaskStartRequest(
                    run_id="second",
                    task="new task",
                    workspace=tmp,
                    agent_name="CodingAgent",
                )
            )
            store.update(
                first,
                TaskCheckpointUpdate(
                    status=TaskRunStatus.BLOCKED,
                    updated_at=1,
                ),
            )
            store.update(
                second,
                TaskCheckpointUpdate(
                    status=TaskRunStatus.WAITING_APPROVAL,
                    updated_at=2,
                ),
            )

            self.assertEqual(
                Path(latest_checkpoint_path(str(run_dir))), store.path_for("second")
            )

    def test_write_resume_link_adds_report_visible_resume_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "new-run"
            source_run = Path(tmp) / "old-run"
            run_dir.mkdir()
            source_run.mkdir()
            report = run_dir / "usage_report.md"
            report.write_text(
                "# Usage Report\n\nExisting evidence.\n", encoding="utf-8"
            )
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

            link = (run_dir / "resume_link.json").read_text(encoding="utf-8")
            chain = (run_dir / "resume_chain.md").read_text(encoding="utf-8")
            report_text = report.read_text(encoding="utf-8")

            self.assertIn("run-old", link)
            self.assertIn("# Resume Chain", chain)
            self.assertIn(str(checkpoint_path), chain)
            self.assertIn("## Resume Chain", report_text)
            self.assertIn(str(source_run), report_text)
            kinds = {
                artifact.kind
                for artifact in read_run_manifest(run_dir / "run_manifest.json").artifacts
            }
            self.assertIn("resume_link", kinds)
            self.assertIn("resume_chain_report", kinds)

    def test_resume_preserves_new_model_instruction_and_tool_policy(self):
        args = build_parser().parse_args(
            [
                "resume",
                "/tmp/old-run",
                "--model-context-window",
                "8192",
                "--no-native-tool-calling",
                "--no-parallel-tool-calls",
                "--instruction-target",
                "src",
                "--global-instruction-file",
                "/tmp/global.md",
                "--runtime-instructions",
                "continue with the reviewed direction",
                "--instruction-max-bytes",
                "2048",
                "--tool",
                "read_file",
            ]
        )
        checkpoint = TaskCheckpoint(
            run_id="run-old",
            task="continue",
            workspace="/tmp/repository",
            status="paused",
        )
        plan = ContinuationPlan(
            task="continue",
            workspace="/tmp/repository",
            human_thread_id="thread-1",
        )
        with mock.patch(
            "agent_forge.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "agent_forge.cli.resume.load_task_checkpoint",
            return_value=checkpoint,
        ), mock.patch(
            "agent_forge.cli.resume.prepare_continuation",
            return_value=(checkpoint, "/tmp/checkpoint.json", plan),
        ), mock.patch(
            "agent_forge.cli.resume.run_repository_task",
            return_value=Path("/tmp/new-run"),
        ) as run, mock.patch("agent_forge.cli.resume.write_resume_link"):
            resume_repository_task(args)

        forwarded = run.call_args.args[0]
        self.assertEqual(forwarded.model_context_window, 8_192)
        self.assertFalse(forwarded.native_tool_calling)
        self.assertFalse(forwarded.parallel_tool_calls)
        self.assertEqual(forwarded.instruction_target, "src")
        self.assertEqual(forwarded.global_instruction_file, ["/tmp/global.md"])
        self.assertEqual(
            forwarded.runtime_instructions,
            "continue with the reviewed direction",
        )
        self.assertEqual(forwarded.instruction_max_bytes, 2_048)
        self.assertEqual(forwarded.enabled_tools, ["read_file"])

    def test_resume_inherits_resolved_config_while_explicit_cli_options_win(self):
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
                            "temperature": 0.7,
                            "thinking_mode": "enabled",
                            "reasoning_effort": "max",
                            "max_steps": 4,
                            "native_tool_calling": True,
                            "approval_mode": "on-risk",
                            "execution_mode": "worktree",
                            "enabled_tools": ["read_file"],
                            "agent_mode": "single",
                            "runtime_instructions_configured": False,
                            "task": "original task",
                            "workspace": str(workspace),
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "resume",
                    str(source_run),
                    "--max-steps",
                    "9",
                    "--no-native-tool-calling",
                ]
            )
            checkpoint = TaskCheckpoint(
                run_id="run-old",
                task="continue",
                workspace=str(workspace),
                status="paused",
            )
            plan = ContinuationPlan(
                task="continue",
                workspace=str(workspace),
                human_thread_id="thread-1",
            )
            with mock.patch(
                "agent_forge.cli.resume.latest_checkpoint_path",
                return_value=str(source_run / "task_state" / "checkpoint.json"),
            ), mock.patch(
                "agent_forge.cli.resume.load_task_checkpoint",
                return_value=checkpoint,
            ), mock.patch(
                "agent_forge.cli.resume.prepare_continuation",
                return_value=(checkpoint, "/tmp/checkpoint.json", plan),
            ), mock.patch(
                "agent_forge.cli.resume.run_repository_task",
                return_value=Path("/tmp/new-run"),
            ) as run, mock.patch("agent_forge.cli.resume.write_resume_link"):
                resume_repository_task(args)

            forwarded = run.call_args.args[0]
            self.assertEqual(forwarded.provider, "openai")
            self.assertEqual(forwarded.model, "source-model")
            self.assertEqual(forwarded.temperature, 0.7)
            self.assertEqual(forwarded.thinking_mode, "enabled")
            self.assertEqual(forwarded.reasoning_effort, "max")
            self.assertEqual(forwarded.max_steps, 9)
            self.assertFalse(forwarded.native_tool_calling)
            self.assertEqual(forwarded.approval_mode, "on-risk")
            self.assertEqual(forwarded.execution_mode, "worktree")
            self.assertEqual(forwarded.enabled_tools, ["read_file"])

    def test_resume_rejects_silently_dropping_redacted_runtime_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_run = Path(tmp) / "old-run"
            source_run.mkdir()
            (source_run / "resolved_config.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "values": {"runtime_instructions_configured": True},
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(["resume", str(source_run)])
            checkpoint = TaskCheckpoint(
                run_id="run-old",
                task="continue",
                workspace=tmp,
                status="paused",
            )
            with mock.patch(
                "agent_forge.cli.resume.latest_checkpoint_path",
                return_value=str(source_run / "task_state" / "checkpoint.json"),
            ), mock.patch(
                "agent_forge.cli.resume.load_task_checkpoint",
                return_value=checkpoint,
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "pass --runtime-instructions explicitly",
                ):
                    resume_repository_task(args)

    def test_resume_fails_closed_when_source_run_has_no_resolved_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_run = Path(tmp) / "old-run"
            source_run.mkdir()
            args = build_parser().parse_args(["resume", str(source_run)])
            checkpoint = TaskCheckpoint(
                run_id="run-old",
                task="continue",
                workspace=tmp,
                status="paused",
            )
            with mock.patch(
                "agent_forge.cli.resume.latest_checkpoint_path",
                return_value=str(source_run / "task_state" / "checkpoint.json"),
            ), mock.patch(
                "agent_forge.cli.resume.load_task_checkpoint",
                return_value=checkpoint,
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "cannot resume without.*resolved_config.json",
                ):
                    resume_repository_task(args)

    def test_resume_without_tool_allowlist_keeps_default_coding_tools(self):
        args = build_parser().parse_args(["resume", "/tmp/old-run"])
        checkpoint = TaskCheckpoint(
            run_id="run-old",
            task="continue",
            workspace="/tmp/repository",
            status="paused",
        )
        plan = ContinuationPlan(
            task="continue",
            workspace="/tmp/repository",
            human_thread_id="thread-1",
        )
        with mock.patch(
            "agent_forge.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "agent_forge.cli.resume.load_task_checkpoint",
            return_value=checkpoint,
        ), mock.patch(
            "agent_forge.cli.resume.prepare_continuation",
            return_value=(checkpoint, "/tmp/checkpoint.json", plan),
        ), mock.patch(
            "agent_forge.cli.resume.run_repository_task",
            return_value=Path("/tmp/new-run"),
        ) as run, mock.patch("agent_forge.cli.resume.write_resume_link"):
            resume_repository_task(args)

        forwarded = run.call_args.args[0]
        self.assertIsNone(forwarded.enabled_tools)

    def test_resume_answers_the_single_pending_human_request(self):
        args = build_parser().parse_args(
            ["resume", "/tmp/old-run", "--answer", "Python 3.11"]
        )
        checkpoint = TaskCheckpoint(
            run_id="run-old",
            task="continue",
            workspace="/tmp/repository",
            status="waiting_human",
        )
        plan = ContinuationPlan(
            task="continue with answer",
            workspace="/tmp/repository",
            human_thread_id="thread-1",
        )
        pending = SimpleNamespace(request_id="request-1")
        with mock.patch(
            "agent_forge.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "agent_forge.cli.resume.load_task_checkpoint",
            return_value=checkpoint,
        ), mock.patch(
            "agent_forge.cli.resume.list_pending_human_inputs",
            return_value=[pending],
        ), mock.patch(
            "agent_forge.cli.resume.respond_to_human_input"
        ) as respond, mock.patch(
            "agent_forge.cli.resume.prepare_continuation",
            return_value=(checkpoint, "/tmp/checkpoint.json", plan),
        ), mock.patch(
            "agent_forge.cli.resume.run_repository_task",
            return_value=Path("/tmp/new-run"),
        ), mock.patch("agent_forge.cli.resume.write_resume_link"):
            resume_repository_task(args)

        command = respond.call_args.args[0]
        self.assertEqual(command.request_id, "request-1")
        self.assertEqual(command.answer, "Python 3.11")

    def test_resume_requires_an_explicit_approval_decision(self):
        args = build_parser().parse_args(["resume", "/tmp/old-run"])
        checkpoint = TaskCheckpoint(
            run_id="run-old",
            task="continue",
            workspace="/tmp/repository",
            status="waiting_approval",
        )
        pending = SimpleNamespace(operation_key="operation-1")
        with mock.patch(
            "agent_forge.cli.resume.latest_checkpoint_path",
            return_value="/tmp/checkpoint.json",
        ), mock.patch(
            "agent_forge.cli.resume.load_task_checkpoint",
            return_value=checkpoint,
        ), mock.patch(
            "agent_forge.cli.resume.list_pending_approvals",
            return_value=[pending],
        ):
            with self.assertRaisesRegex(SystemExit, "--decision approved"):
                resume_repository_task(args)


if __name__ == "__main__":
    unittest.main()
