import tempfile
import unittest
from pathlib import Path

from agent_forge.context.repo_map import build_repo_map
from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.runtime.adapters.context_assembler import (
    RepositoryTurnSystemContextAssembler,
)
from agent_forge.runtime.ports.context import TurnSystemContextRequest


class RepositoryTurnSystemContextAssemblerTest(unittest.TestCase):
    def test_repo_map_does_not_ignore_workspace_because_of_parent_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".agent_forge" / "runs" / "case" / "workspace"
            source = workspace / "src" / "module.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")

            repo_map = build_repo_map(workspace)

        self.assertEqual(repo_map, "src/module.py")

    def test_builds_bounded_context_from_repository_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "FORGE.md").write_text(
                "Always inspect target.py before editing.\n",
                encoding="utf-8",
            )

            report = RepositoryTurnSystemContextAssembler().build(
                TurnSystemContextRequest(
                    task="inspect target.py without editing",
                    workspace=tmp,
                    working_memory=WorkingMemory(),
                    tool_schemas=[
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

            report = RepositoryTurnSystemContextAssembler().build(
                TurnSystemContextRequest(
                    task="inspect every target module",
                    workspace=tmp,
                    working_memory=memory,
                    tool_schemas=[
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
