import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.runtime.adapters.context_assembler import RepositoryContextAssembler


class RepositoryContextAssemblerTest(unittest.TestCase):
    def test_builds_bounded_context_from_repository_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "FORGE.md").write_text(
                "Always inspect target.py before editing.\n",
                encoding="utf-8",
            )

            report = RepositoryContextAssembler().build(
                task="inspect target.py without editing",
                workspace=tmp,
                working_memory=WorkingMemory(),
                tools=[
                    {
                        "name": "read_file",
                        "description": "Read one file",
                        "arguments": {"path": "str"},
                    }
                ],
                active_skill_cards=[],
                max_chars=4000,
                permission_summary="read allowed",
            )

        self.assertIn("target.py", report.selected_files)
        self.assertIn("Always inspect target.py", report.project_instructions)
        self.assertEqual(report.available_tools, ["read_file"])
        self.assertLessEqual(len(report.repo_map), 1000)
        self.assertLessEqual(len(report.render()), report.max_chars)

    def test_static_context_budget_covers_every_rendered_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(12):
                (root / f"target_{index}.py").write_text(
                    f"VALUE_{index} = " + ("x" * 2_000),
                    encoding="utf-8",
                )
            (root / "FORGE.md").write_text(
                "project policy " * 500,
                encoding="utf-8",
            )
            memory = WorkingMemory()
            for index in range(10):
                memory.add("working-memory-" + str(index) + ("m" * 500))

            report = RepositoryContextAssembler().build(
                task="inspect every target module",
                workspace=tmp,
                working_memory=memory,
                tools=[
                    {
                        "name": "read_file",
                        "description": "Read one file",
                        "arguments": {"path": "str"},
                    }
                ],
                active_skill_cards=["skill guidance " * 300],
                max_chars=1_000,
                permission_summary="read allowed; writes require approval",
            )

        rendered = report.render()
        self.assertLessEqual(len(rendered), 1_000)
        self.assertEqual(report.total_chars, len(rendered))
        self.assertTrue(report.truncated)
        self.assertIn("system:", rendered)
        self.assertIn("permission_summary:", rendered)
        self.assertTrue(
            any("context budget" in item for item in report.dropped_context)
        )


if __name__ == "__main__":
    unittest.main()
