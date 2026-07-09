import unittest

from agent_forge.safety.command_policy import command_policy_summary
from agent_forge.tools.tool_router import ToolRouter


class ToolRouterPolicySummaryTest(unittest.TestCase):
    def test_command_policy_summary_names_restricted_shell_behavior(self):
        summary = command_policy_summary()
        self.assertFalse(summary["free_form_shell"])
        self.assertIn("diagnostics", " ".join(summary["preferred_validation_tools"]))
        self.assertIn("destructive commands", summary["blocked_patterns"])

    def test_tool_router_policy_summary_lists_visible_and_hidden_tools(self):
        schemas = [
            {"name": "read_file"},
            {"name": "apply_patch"},
            {"name": "run_command"},
        ]
        route = ToolRouter().route("read only inspect the file", schemas)

        summary = route.policy_summary()

        self.assertEqual(summary["allowed_tools"], ["read_file"])
        self.assertEqual(summary["hidden_tools"], ["apply_patch", "run_command"])
        self.assertEqual(summary["tool_count"], {"allowed": 1, "hidden": 2})


if __name__ == "__main__":
    unittest.main()
