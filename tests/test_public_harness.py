import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_forge
from agent_forge import Harness, HarnessConfig, HarnessExtensions, RunRequest, TaskRunStatus
from agent_forge.extensions import ToolRegistry
from agent_forge.runtime.domain.conversation import (
    AgentResponse,
    Message,
    Observation,
    ToolCall,
)
from agent_forge.runtime.adapters import (
    JsonConversationThreadRepository,
    JsonTaskStateRepository,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.base import Tool
from agent_forge.tools.builtins.read_file import ReadFileTool
from tests.support import StaticResponseModel


def _final_model() -> StaticResponseModel:
    return StaticResponseModel("completed through the public Harness API")


class DurableConversationModel:
    """首个 Turn 读取事实但不复述；新进程中的 Follow-up 只靠 Thread 回答。"""

    last_usage = None

    def __init__(self, *, follow_up: bool = False) -> None:
        self.follow_up = follow_up
        self.calls = 0

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        self.calls += 1
        tool_messages = [message.content for message in messages if message.role == "tool"]
        if self.follow_up:
            recalled = "7319" if any("7319" in item for item in tool_messages) else "unknown"
            return AgentResponse(f"The previous needle was {recalled}.", [])
        if len(tool_messages) == 0:
            return AgentResponse(
                "",
                [ToolCall("read-a", "read_file", {"path": "a.txt"})],
            )
        if len(tool_messages) == 1:
            return AgentResponse(
                "",
                [ToolCall("read-b", "read_file", {"path": "b.txt"})],
            )
        return AgentResponse("Repository inspection completed.", [])


class EphemeralObservationTool(Tool):
    name = "ephemeral_observation"
    description = "Return one runtime-only observation for ownership testing."

    def schema(self):
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {},
            "required": [],
        }

    def execute(self, arguments):
        return Observation(self.name, True, "UNIQUE_RUNTIME_OBSERVATION_7319")


class ObservationDedupModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0
        self.marker_count = 0

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "",
                [ToolCall("observation-1", "ephemeral_observation", {})],
            )
        self.marker_count = sum(
            (message.content or "").count("UNIQUE_RUNTIME_OBSERVATION_7319")
            for message in messages
        )
        return AgentResponse("Observation ownership verified.", [])


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
                model=_final_model(),
                tools=ToolRegistry(),
                config=HarnessConfig(enabled_tools=("read_file",)),
            )
        with self.assertRaisesRegex(ValueError, "use lifecycle_hooks"):
            Harness(
                model=_final_model(),
                extensions=HarnessExtensions(hook_policy=object()),
            )

    def test_tool_observation_has_one_model_input_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = ObservationDedupModel()
            registry = ToolRegistry()
            registry.register(EphemeralObservationTool())

            result = Harness(
                model=model,
                tools=registry,
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    tool_routing_mode="all",
                    skill_mode="none",
                    max_steps=3,
                ),
            ).run("Read the one runtime observation and finish.")

            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            self.assertEqual(model.marker_count, 1)

    def test_external_consumer_runs_without_importing_runtime_internals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = Harness(
                model=_final_model(),
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
            self.assertTrue(result.candidate_diff_path and result.candidate_diff_path.exists())
            self.assertTrue(result.manifest_path and result.manifest_path.exists())
            request_artifact = json.loads(
                (result.artifact_dir / "run_request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request_artifact["schema_version"], 1)
            self.assertEqual(
                request_artifact["request"]["task"],
                "Summarize the repository architecture and finish.",
            )
            self.assertEqual(result.checkpoint.thread_id, result.thread_id)
            self.assertEqual(result.checkpoint.turn_id, result.turn_id)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            artifact_paths = {
                item["relative_path"] for item in manifest["artifacts"]
            }
            self.assertIn("trace.json", artifact_paths)
            self.assertIn("candidate_changes.diff", artifact_paths)

            # 发现入口只指向原生 run；系统不再维护第二套派生证据树。
            latest_run = root / ".agent_forge" / "internal" / "index" / "run.txt"
            self.assertEqual(
                Path(latest_run.read_text(encoding="utf-8")).resolve(),
                result.artifact_dir.resolve(),
            )
            self.assertFalse((root / ".agent_forge" / "runtime_evidence").exists())
            self.assertEqual(
                {path.name for path in (root / ".agent_forge").iterdir()},
                {"archive", "internal", "runs"},
            )
            state_root = root / ".agent_forge" / "internal" / "state"
            for name in (
                "approvals",
                "human_input",
                "operation_ledger",
                "threads",
            ):
                self.assertTrue((state_root / name).is_dir())

    def test_custom_checkpoint_repository_owns_bootstrap_and_thread_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom_states = JsonTaskStateRepository(root / "custom-state")
            result = Harness(
                model=_final_model(),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    conversation_thread_root=str(root / "threads"),
                ),
                extensions=HarnessExtensions(
                    checkpoint_repository=custom_states,
                ),
            ).run("finish through a custom checkpoint repository")

            checkpoint_path = custom_states.path_for(result.run_id).resolve()
            self.assertTrue(checkpoint_path.is_file())
            thread = JsonConversationThreadRepository(root / "threads").get(
                result.thread_id
            )
            assert thread is not None
            self.assertEqual(
                Path(thread.latest_run.checkpoint_path).resolve(),  # type: ignore[union-attr]
                checkpoint_path,
            )

    def test_terminal_follow_up_creates_new_turn_in_same_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = Harness(
                model=_final_model(),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=2,
                ),
            )
            first = harness.run("Summarize the current repository architecture.")
            follow_up = harness.run(
                RunRequest(
                    "Explain the validation evidence.",
                    thread_id=first.thread_id,
                )
            )

            self.assertNotEqual(follow_up.run_id, first.run_id)
            self.assertEqual(follow_up.status, TaskRunStatus.COMPLETED)
            self.assertEqual(follow_up.thread_id, first.thread_id)
            self.assertNotEqual(follow_up.turn_id, first.turn_id)

    def test_short_thread_survives_process_restart_without_compaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("alpha=1\n", encoding="utf-8")
            (root / "b.txt").write_text("needle=7319\n", encoding="utf-8")
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            config = HarnessConfig(
                workspace=str(root),
                output_root=str(root / "runs"),
                max_steps=4,
                skill_mode="none",
                tool_routing_mode="all",
            )

            first_model = DurableConversationModel()
            first = Harness(model=first_model, tools=registry, config=config).run(
                "Inspect a.txt and b.txt, then finish without quoting their values."
            )
            self.assertEqual(first.status, TaskRunStatus.COMPLETED)
            self.assertNotIn("7319", first.final_answer or "")

            # 重建 Harness 与 Model，证明答案来自 durable Thread，而非进程内 Session。
            follow_up_model = DurableConversationModel(follow_up=True)
            follow_up = Harness(
                model=follow_up_model,
                tools=registry,
                config=config,
            ).run(
                RunRequest(
                    "What was the needle value in the second file?",
                    thread_id=first.thread_id,
                )
            )
            self.assertEqual(follow_up_model.calls, 1)
            self.assertIn("7319", follow_up.final_answer or "")
            self.assertEqual(follow_up.thread_id, first.thread_id)
            self.assertNotEqual(follow_up.turn_id, first.turn_id)
            request_payload = json.loads(
                (follow_up.artifact_dir / "run_request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request_payload["request"]["resume_state"], "")

    def test_owned_environment_is_cleaned_when_runtime_assembly_fails(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "agent_forge.harness.build_registry",
            side_effect=RuntimeError("registry assembly failed"),
        ), mock.patch(
            "agent_forge.harness.ExecutionEnvironment.cleanup",
            autospec=True,
        ) as cleanup:
            harness = Harness(
                model=_final_model(),
                config=HarnessConfig(
                    workspace=tmp,
                    output_root=str(Path(tmp) / "runs"),
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "registry assembly failed"):
                harness.run("exercise setup cleanup")

            cleanup.assert_called_once()
            manifest_path = next((Path(tmp) / "runs").glob("*/run_manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["stop_reason"], "exception:RuntimeError")

    def test_snapshot_build_failure_does_not_publish_active_turn(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "agent_forge.runtime.application.run_preparation."
            "RunPreparation.build_new_turn_snapshot",
            side_effect=OSError("snapshot build failed"),
        ):
            root = Path(tmp)
            threads_root = root / "threads"
            harness = Harness(
                model=_final_model(),
                tools=ToolRegistry(),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    conversation_thread_root=str(threads_root),
                ),
            )

            with self.assertRaisesRegex(OSError, "snapshot build failed"):
                harness.run("freeze before publishing the Turn")

            threads = JsonConversationThreadRepository(threads_root).list_all()
            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0].active_turn_id, "")
            self.assertEqual(threads[0].turns, ())
            self.assertEqual(list((root / "runs").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
