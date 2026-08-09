import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_forge.runtime.application.turn_preparation import TurnPreparation
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
            self.assertFalse(hasattr(discovered[0], "loaded_resources"))
            self.assertEqual(discovered[0].source, str(manifest.resolve()))
            activated = registry.activate(discovered[0])
            self.assertIn("Inspect the lockfile", activated.prompt_card())
            self.assertEqual(activated.tool_names, ["read_file"])
            self.assertEqual(activated.required_tool_names, [])
            self.assertEqual(activated.optional_tool_names, ["read_file"])

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
        self.assertIn("smallest evidence-backed repository repair", prompt_card)
        self.assertIn("Repository repair workflow", prompt_card)
        self.assertLess(len(prompt_card), 1_500)
        self.assertNotIn("done_criteria:", prompt_card)
        self.assertNotIn("failure_recovery:", prompt_card)
        self.assertNotIn("permissions:", prompt_card)
        self.assertNotIn("dependencies:", prompt_card)
        self.assertNotIn("tools:", prompt_card)
        self.assertEqual(selected[0].loaded_resources, ())

    def test_package_discloses_only_one_task_matched_reference(self):
        registry = build_default_skill_registry()

        selected = registry.select_for_task(
            "Resolve this SWE-bench failing pytest traceback.",
            names=["swebench_repair"],
        )

        self.assertEqual(len(selected[0].loaded_resources), 1)
        resource = selected[0].loaded_resources[0]
        self.assertEqual(resource.path, "references/failure-triage.md")
        self.assertIn("first stable failure signal", selected[0].prompt_card())
        self.assertNotIn("Choose evidence in this order", selected[0].prompt_card())
        self.assertLessEqual(resource.disclosed_chars, 1_400)
        self.assertEqual(len(resource.sha256), 64)

    def test_package_rejects_resource_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "unsafe"
            package.mkdir()
            (root / "secret.md").write_text("secret", encoding="utf-8")
            (package / "SKILL.md").write_text(
                """---
name: unsafe
version: 1.0.0
description: unsafe package
entrypoint: workflow:unsafe
resources:
  - path: ../secret.md
    description: must not escape
---
# Unsafe
""",
                encoding="utf-8",
            )

            registry = SkillRegistry()
            with self.assertRaisesRegex(ValueError, "escapes package"):
                registry.load_package(package)

    def test_required_tool_is_checked_before_model_call(self):
        registry = build_default_skill_registry()
        selected = registry.select_for_task(
            "Resolve this SWE-bench issue.",
            names=["swebench_repair"],
        )
        session = SimpleNamespace(active_skills=selected)

        with self.assertRaisesRegex(ValueError, "requires unavailable tools"):
            TurnPreparation._verify_skill_tool_dependencies(
                session=session,
                registered_tool_schemas=[{"name": "read_file"}],
            )


if __name__ == "__main__":
    unittest.main()
