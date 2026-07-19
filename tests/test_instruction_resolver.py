import tempfile
import unittest
from pathlib import Path

from agent_forge.context.instructions import (
    InstructionResolutionRequest,
    resolve_instructions,
)


class InstructionResolverTest(unittest.TestCase):
    def test_hierarchy_and_provenance_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "src" / "feature"
            nested.mkdir(parents=True)
            global_file = root / "global.md"
            global_file.write_text("global rule", encoding="utf-8")
            (root / "AGENTS.md").write_text("repository rule", encoding="utf-8")
            (root / "src" / "CLAUDE.md").write_text(
                "directory rule", encoding="utf-8"
            )
            (nested / "AGENTS.override.md").write_text(
                "local override", encoding="utf-8"
            )

            result = resolve_instructions(
                InstructionResolutionRequest(
                    workspace=root,
                    active_path="src/feature",
                    global_files=(str(global_file),),
                    runtime_override="runtime override",
                    max_bytes=10_000,
                )
            )

            expected = [
                "global rule",
                "repository rule",
                "directory rule",
                "local override",
                "runtime override",
            ]
            positions = [result.content.index(value) for value in expected]
            self.assertEqual(positions, sorted(positions))
            evidence = result.to_evidence()
            self.assertEqual(
                [source["kind"] for source in evidence["sources"]],
                [
                    "global",
                    "repository",
                    "directory",
                    "local_override",
                    "runtime_override",
                ],
            )
            self.assertTrue(all(source["sha256"] for source in evidence["sources"]))
            self.assertFalse(result.truncated)

    def test_budget_preserves_high_priority_runtime_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "FORGE.md").write_text("low-" * 100, encoding="utf-8")

            result = resolve_instructions(
                InstructionResolutionRequest(
                    workspace=root,
                    runtime_override="must-keep",
                    max_bytes=20,
                )
            )

            self.assertIn("must-keep", result.content)
            self.assertTrue(result.truncated)
            self.assertLess(
                result.sources[0].included_bytes,
                result.sources[0].original_bytes,
            )

    def test_active_path_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "escapes workspace"):
                resolve_instructions(
                    InstructionResolutionRequest(
                        workspace=root,
                        active_path="../outside",
                    )
                )


if __name__ == "__main__":
    unittest.main()
