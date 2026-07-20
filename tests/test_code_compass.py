import unittest

from agent_forge.code_compass import inspect_symbol, render_symbol_card


class CodeCompassTest(unittest.TestCase):
    CORE_OWNER_SYMBOLS = (
        "Harness.run",
        "AgentLoop.run",
        "RunPreparation.start",
        "RunPreparation.execute",
        "TurnPreparation.execute",
        "ToolExecutionPipeline.execute_calls",
        "RunLifecycle.stop",
        "FinalAnswerBuilder.execute",
    )

    def test_resolves_core_method_and_exposes_static_navigation(self):
        card = inspect_symbol("AgentLoop.run")

        self.assertEqual(card.kind, "method")
        self.assertEqual(card.layer, "application")
        self.assertEqual(
            card.source_path.as_posix(),
            "agent_forge/runtime/application/agent_loop.py",
        )
        self.assertIn("Harness.run", card.canonical_upstream)
        self.assertIn("RunPreparation", card.next_owner)
        self.assertIn("RunLifecycle.stop", card.invariant)
        self.assertIn("test_agent_loop", " ".join(card.behavior_tests))
        self.assertIn("Code Compass", render_symbol_card(card))

    def test_core_owners_answer_the_navigation_contract_without_fallbacks(self):
        for symbol in self.CORE_OWNER_SYMBOLS:
            with self.subTest(symbol=symbol):
                card = inspect_symbol(symbol)
                self.assertTrue(card.flow_position)
                self.assertTrue(card.canonical_upstream)
                self.assertTrue(card.next_owner)
                self.assertTrue(card.state_or_evidence)
                self.assertTrue(card.invariant)
                self.assertTrue(card.deletion_impact)

    def test_rejects_unknown_symbol_without_guessing(self):
        with self.assertRaisesRegex(ValueError, "source symbol not found"):
            inspect_symbol("ImaginaryRuntimeOwner.execute")


if __name__ == "__main__":
    unittest.main()
