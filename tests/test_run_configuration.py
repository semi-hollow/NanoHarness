import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_forge.cli.parser import build_parser
from agent_forge.cli.repository import run_repository_task
from agent_forge.configuration import (
    load_run_config,
    resolve_run_arguments,
    resolved_run_config,
)
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry
from agent_forge.extensions import AgentResponse


class FinalModel:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse("configuration path completed", [])


class RunConfigurationTest(unittest.TestCase):
    def test_cli_and_environment_override_versioned_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.yaml"
            path.write_text(
                """schema_version: 1
run:
  task: task from config
  workspace: /config/workspace
model:
  model: config-model
runtime:
  max_steps: 3
tools:
  enabled: [read_file, grep]
""",
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                ["run", "task from cli", "--config", str(path), "--max-steps", "9"]
            )
            with mock.patch.dict(
                os.environ,
                {"AGENT_FORGE_MODEL": "environment-model"},
                clear=True,
            ):
                document = resolve_run_arguments(args)

            self.assertEqual(args.task, "task from cli")
            self.assertEqual(args.max_steps, 9)
            self.assertEqual(args.workspace, "/config/workspace")
            self.assertEqual(args.model, "environment-model")
            self.assertEqual(args.enabled_tools, ["read_file", "grep"])
            artifact = resolved_run_config(args, document)
            self.assertEqual(
                artifact["precedence"],
                "cli > model_environment > config > defaults",
            )
            self.assertNotIn("api_key", artifact["values"])

    def test_config_can_supply_task_and_defaults_fill_remaining_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.yaml"
            path.write_text(
                """schema_version: 1
run:
  task: inspect the repository safely
policy:
  approval_mode: locked
  auto_approve_writes: false
""",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["run", "--config", str(path)])
            with mock.patch.dict(os.environ, {}, clear=True):
                resolve_run_arguments(args)

            self.assertEqual(args.task, "inspect the repository safely")
            self.assertEqual(args.approval_mode, "locked")
            self.assertFalse(args.auto_approve_writes)
            self.assertEqual(args.provider, "deepseek")
            self.assertEqual(args.execution_mode, "local")

    def test_model_capabilities_and_runtime_instruction_are_typed_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.yaml"
            path.write_text(
                """schema_version: 1
run:
  task: inspect the repository
model:
  context_window: 8192
  native_tool_calling: false
  parallel_tool_calls: false
instructions:
  target: src
  runtime_override: private runtime direction
  max_bytes: 2048
""",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["run", "--config", str(path)])
            with mock.patch.dict(os.environ, {}, clear=True):
                document = resolve_run_arguments(args)

            self.assertEqual(args.model_context_window, 8_192)
            self.assertFalse(args.native_tool_calling)
            self.assertFalse(args.parallel_tool_calls)
            self.assertEqual(args.instruction_target, "src")
            artifact = resolved_run_config(args, document)
            encoded = json.dumps(artifact)
            self.assertNotIn("private runtime direction", encoded)
            self.assertTrue(
                artifact["values"]["runtime_instructions_configured"]
            )
            self.assertTrue(artifact["values"]["runtime_instructions_sha256"])

    def test_invalid_final_budget_values_fail_before_a_run_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.yaml"
            path.write_text(
                """schema_version: 1
run:
  task: inspect the repository
runtime:
  max_prompt_tokens: 4096
  reserved_output_tokens: 4096
""",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["run", "--config", str(path)])

            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
                ValueError,
                "reserved_output_tokens must be below max_prompt_tokens",
            ):
                resolve_run_arguments(args)

    def test_unknown_and_secret_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = root / "unknown.yaml"
            unknown.write_text(
                "schema_version: 1\nruntime:\n  magic_retry: 3\n",
                encoding="utf-8",
            )
            secret = root / "secret.yaml"
            secret.write_text(
                "schema_version: 1\nmodel:\n  api_key: should-not-be-here\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown fields"):
                load_run_config(unknown)
            with self.assertRaisesRegex(ValueError, "use environment variables"):
                load_run_config(secret)

    def test_tool_allowlist_changes_the_real_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_registry(
                ToolRegistryBuildRequest(
                    workspace=tmp,
                    auto=True,
                    enabled_tools=("read_file", "grep"),
                )
            )
            self.assertEqual(
                {schema["name"] for schema in registry.schemas()},
                {"read_file", "grep"},
            )
            empty_registry = build_registry(
                ToolRegistryBuildRequest(
                    workspace=tmp,
                    auto=True,
                    enabled_tools=(),
                )
            )
            self.assertEqual(empty_registry.schemas(), [])
            with self.assertRaisesRegex(ValueError, "unknown built-in tools"):
                build_registry(
                    ToolRegistryBuildRequest(
                        workspace=tmp,
                        auto=True,
                        enabled_tools=("imaginary_tool",),
                    )
                )

    def test_config_drives_the_real_cli_run_and_publishes_resolved_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "agent.yaml"
            config.write_text(
                f"""schema_version: 1
run:
  task: Summarize the repository architecture and finish.
  workspace: {root}
  output_root: {root / 'runs'}
model:
  provider: deepseek
  model: test-model
policy:
  approval_mode: locked
tools:
  enabled: []
""",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["run", "--config", str(config)])
            with mock.patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "test-secret-value"},
                clear=True,
            ), mock.patch(
                "agent_forge.cli.repository.build_llm",
                return_value=FinalModel(),
            ):
                run_dir = run_repository_task(args)

            resolved = json.loads(
                (run_dir / "resolved_config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(resolved["values"]["model"], "test-model")
            self.assertTrue(resolved["api_key_configured"])
            self.assertNotIn("test-secret-value", str(resolved))
            self.assertEqual(
                (run_dir / "final_answer.txt").read_text(encoding="utf-8").splitlines()[0],
                "configuration path completed",
            )


if __name__ == "__main__":
    unittest.main()
