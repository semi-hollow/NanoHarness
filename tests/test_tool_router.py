import unittest

from agent_forge.safety.command_policy import command_policy_summary
from agent_forge.tools.tool_router import ToolRouter, ToolRoutingRequest


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
        route = ToolRouter().route(
            ToolRoutingRequest(
                task="read only inspect the file",
                schemas=schemas,
            )
        )

        summary = route.policy_summary()

        self.assertEqual(summary["allowed_tools"], ["read_file"])
        self.assertEqual(summary["hidden_tools"], ["apply_patch", "run_command"])
        self.assertEqual(summary["tool_count"], {"allowed": 1, "hidden": 2})

    def test_all_mode_is_an_observable_ablation_without_hiding_tools(self):
        schemas = [
            {"name": "read_file"},
            {"name": "apply_patch"},
            {"name": "run_command"},
        ]

        route = ToolRouter().route(
            ToolRoutingRequest(
                task="read only inspect the file",
                schemas=schemas,
                mode="all",
            )
        )

        self.assertEqual(
            route.allowed_names, {"read_file", "apply_patch", "run_command"}
        )
        self.assertEqual(route.dropped_names, [])
        self.assertIn("mode=all", route.reason)

    def test_durable_human_input_control_is_visible_without_prompt_keywords(self):
        schemas = [
            {"name": "read_file"},
            {"name": "ask_human"},
            {"name": "apply_patch"},
        ]

        route = ToolRouter().route(
            ToolRoutingRequest(
                task="inspect the runtime and decide what evidence is missing",
                schemas=schemas,
            )
        )

        self.assertIn("ask_human", route.allowed_names)
        self.assertEqual(route.metadata["ask_human"]["mode"], "human")


if __name__ == "__main__":
    unittest.main()
