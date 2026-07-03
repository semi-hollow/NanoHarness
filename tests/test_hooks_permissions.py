import tempfile
import unittest

from agent_forge.runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig
from agent_forge.runtime.hooks import HookContext, HookDecisionType, HookManager


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


if __name__ == "__main__":
    unittest.main()
