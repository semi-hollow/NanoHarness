import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.skills import SkillRegistry, build_default_skill_registry


class SkillDisclosureTest(unittest.TestCase):
    def test_discovery_returns_metadata_before_full_skill_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "skill.json"
            manifest.write_text(
                json.dumps(
                    {
                        "name": "dependency_review",
                        "version": "1.2.0",
                        "description": "Review dependency risk",
                        "entrypoint": "prompt:dependency_review",
                        "tags": ["dependency", "review"],
                        "activation_terms": ["dependency"],
                        "tool_names": ["read_file"],
                        "operating_procedure": ["Inspect the lockfile"],
                        "done_criteria": ["Report risky dependencies"],
                    }
                ),
                encoding="utf-8",
            )
            registry = SkillRegistry()
            registry.load_manifest(manifest)

            discovered = registry.discover_for_task("review dependency changes")

            self.assertEqual(len(discovered), 1)
            self.assertFalse(hasattr(discovered[0], "operating_procedure"))
            self.assertEqual(discovered[0].source, str(manifest.resolve()))
            activated = registry.activate(discovered[0])
            self.assertIn("Inspect the lockfile", activated.prompt_card())
            self.assertEqual(activated.tool_names, ["read_file"])

    def test_test_protection_does_not_hide_repair_skills(self):
        registry = build_default_skill_registry()

        discovered = registry.discover_for_task(
            "Repair the settlement service. Do not modify tests. Run pytest."
        )

        self.assertIn("bug_fix", {entry.name for entry in discovered})

    def test_auto_selection_activates_one_primary_workflow(self):
        registry = build_default_skill_registry()

        selected = registry.select_for_task(
            "Fix the failing pytest and update the implementation docs."
        )

        self.assertEqual(len(selected), 1)

    def test_explicit_swebench_skill_uses_compact_prompt_card(self):
        registry = build_default_skill_registry()

        selected = registry.select_for_task(
            "Resolve this SWE-bench coding issue.",
            names=["swebench_repair"],
        )
        prompt_card = selected[0].prompt_card()

        self.assertEqual([skill.name for skill in selected], ["swebench_repair"])
        self.assertIn("preserve the final two turns", prompt_card)
        self.assertNotIn("permissions:", prompt_card)
        self.assertNotIn("dependencies:", prompt_card)
        self.assertNotIn("tools:", prompt_card)


if __name__ == "__main__":
    unittest.main()
