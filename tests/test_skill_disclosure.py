import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.skills import SkillRegistry


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


if __name__ == "__main__":
    unittest.main()
