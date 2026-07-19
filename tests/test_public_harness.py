import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_forge
from agent_forge import Harness, HarnessConfig, HarnessExtensions, RunRequest, TaskRunStatus
from agent_forge.extensions import AgentResponse, ToolRegistry


class FinalModel:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse("completed through the public Harness API", [])


class PublicHarnessTest(unittest.TestCase):
    def test_top_level_surface_exposes_only_stable_facade_types(self):
        self.assertEqual(
            set(agent_forge.__all__),
            {
                "Harness",
                "HarnessConfig",
                "HarnessExtensions",
                "ModelCapabilities",
                "RunController",
                "RunRequest",
                "RunResult",
                "RuntimeHook",
                "TaskRunStatus",
                "__version__",
            },
        )
        self.assertEqual(agent_forge.__version__, "0.8.0")

    def test_public_config_rejects_ambiguous_or_invalid_runtime_policy(self):
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be positive"):
            HarnessConfig(timeout_seconds=0)
        with self.assertRaisesRegex(ValueError, "only applies to the built-in"):
            Harness(
                model=FinalModel(),
                tools=ToolRegistry(),
                config=HarnessConfig(enabled_tools=("read_file",)),
            )
        with self.assertRaisesRegex(ValueError, "use lifecycle_hooks"):
            Harness(
                model=FinalModel(),
                extensions=HarnessExtensions(hook_policy=object()),
            )

    def test_external_consumer_runs_without_importing_runtime_internals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = Harness(
                model=FinalModel(),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    approval_mode="locked",
                    max_steps=2,
                ),
            )

            result = harness.run(
                RunRequest("Summarize the repository architecture and finish.")
            )

            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            self.assertEqual(result.stop_reason, "final_answer")
            self.assertTrue(
                result.final_answer.startswith(
                    "completed through the public Harness API"
                )
            )
            self.assertTrue(result.trace_path and result.trace_path.exists())
            self.assertTrue(result.usage_path and result.usage_path.exists())
            self.assertTrue(result.patch_path and result.patch_path.exists())
            request_artifact = json.loads(
                (result.artifact_dir / "run_request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request_artifact["schema_version"], 1)
            self.assertEqual(request_artifact["request"]["task"], result.checkpoint.task)

    def test_resume_creates_a_new_trace_and_preserves_checkpoint_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = Harness(
                model=FinalModel(),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=2,
                ),
            )
            first = harness.run("Summarize the current repository architecture.")
            checkpoint_path = first.artifact_dir / "task_state" / f"{first.run_id}.json"

            resumed = harness.resume(checkpoint_path)

            self.assertNotEqual(resumed.run_id, first.run_id)
            self.assertEqual(resumed.status, TaskRunStatus.COMPLETED)
            trace = json.loads(resumed.trace_path.read_text(encoding="utf-8"))
            event_types = [event["event_type"] for event in trace["events"]]
            self.assertIn("resume_state_loaded", event_types)

    def test_owned_environment_is_cleaned_when_runtime_assembly_fails(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "agent_forge.harness.build_registry",
            side_effect=RuntimeError("registry assembly failed"),
        ), mock.patch(
            "agent_forge.harness.ExecutionEnvironment.cleanup",
            autospec=True,
        ) as cleanup:
            harness = Harness(
                model=FinalModel(),
                config=HarnessConfig(
                    workspace=tmp,
                    output_root=str(Path(tmp) / "runs"),
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "registry assembly failed"):
                harness.run("exercise setup cleanup")

            cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
