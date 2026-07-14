import tempfile
import unittest
from pathlib import Path

from agent_forge.context.memory import Memory
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
                memory=Memory(),
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


if __name__ == "__main__":
    unittest.main()
