import tempfile
import unittest

from agent_forge.hooks import RuntimeHook
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.hooks import HookContext, HookDecisionType, HookManager


class BrokenBeforeToolHook(RuntimeHook):
    name = "broken_before_tool"

    def before_tool(self, context):
        raise RuntimeError("custom policy unavailable")


class SecretInjectingHook(RuntimeHook):
    name = "secret_injector"

    def after_tool(self, context, observation):
        return Observation(
            observation.tool_name,
            observation.success,
            "api_key=abcdefghijklmnop",
        )


class HooksPermissionsTest(unittest.TestCase):
    def test_dry_run_denies_write_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = HookManager.default(
                ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=tmp)),
                auto_approve_writes=True,
                approval_mode="dry-run",
            )
            result = manager.pre_tool(
                HookContext(
                    run_id="r1",
                    step=1,
                    agent_name="agent",
                    tool_name="write_file",
                    arguments={"path": "a.txt"},
                    action="write",
                    approval_mode="dry-run",
                )
            )
            self.assertEqual(result.decision, HookDecisionType.DENY)

    def test_on_risk_asks_for_command_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = HookManager.default(
                ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=tmp)),
                auto_approve_writes=True,
                approval_mode="on-risk",
            )
            result = manager.pre_tool(
                HookContext(
                    run_id="r1",
                    step=1,
                    agent_name="agent",
                    tool_name="run_command",
                    arguments={"command": "python -m unittest discover tests"},
                    action="run_command",
                    command="python -m unittest discover tests",
                    approval_mode="on-risk",
                )
            )
            self.assertEqual(result.decision, HookDecisionType.ASK)

    def test_custom_pre_hook_failure_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = HookManager.default(
                ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=tmp)),
                additional_hooks=[BrokenBeforeToolHook()],
            )

            result = manager.pre_tool(
                HookContext(
                    run_id="r1",
                    step=1,
                    agent_name="agent",
                    tool_name="read_file",
                    arguments={"path": "README.md"},
                    action="read",
                )
            )

            self.assertEqual(result.decision, HookDecisionType.DENY)
            self.assertIn("hook failed", result.reason)

    def test_secret_redaction_remains_last_after_custom_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = HookManager.default(
                ExecutionEnvironment(ExecutionEnvironmentConfig(workspace=tmp)),
                additional_hooks=[SecretInjectingHook()],
            )
            context = HookContext(
                run_id="r1",
                step=1,
                agent_name="agent",
                tool_name="read_file",
                arguments={"path": "README.md"},
                action="read",
            )

            result = manager.post_tool(
                context,
                Observation("read_file", True, "safe"),
            )

            self.assertEqual(result.content, "api_key=[redacted]")


if __name__ == "__main__":
    unittest.main()
