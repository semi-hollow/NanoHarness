import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.context.adapters.repository_map import build_repo_map
from agent_forge.runtime.adapters.context_assembler import (
    RepositoryTurnSystemContextAssembler,
)
from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.runtime.ports.context import (
    StableTurnContextRequest,
    TurnSystemContextRequest,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.create_file import CreateFileTool
from agent_forge.tools.builtins.run_command import RunCommandTool


def _stable_request(
    workspace: str,
    *,
    profile: str = "single_agent",
    max_chars: int = 6_000,
) -> StableTurnContextRequest:
    return StableTurnContextRequest(
        root_task="inspect target.py",
        workspace=workspace,
        base_tool_schemas=[{"name": "read_file", "arguments": {"path": "str"}}],
        active_skill_cards=[],
        long_term_memory=[],
        max_chars=max_chars,
        system_prompt_profile=profile,
    )


def _dynamic_request(
    workspace: str,
    stable_prefix: str,
    *,
    max_chars: int = 2_000,
) -> TurnSystemContextRequest:
    return TurnSystemContextRequest(
        turn_focus="inspect target.py",
        stable_system_prefix=stable_prefix,
        workspace=workspace,
        working_memory=WorkingMemory(),
        tool_schemas=[{"name": "read_file", "arguments": {"path": "str"}}],
        max_chars=max_chars,
        permission_summary="read allowed",
    )


class RepositoryTurnSystemContextAssemblerTest(unittest.TestCase):
    def test_system_prompt_profile_is_frozen_by_execution_role(self) -> None:
        expected = {
            "single_agent": "single_agent_system@2026-08-role-aware-v1",
            "fanout_worker": "fanout_worker_system@2026-08-role-aware-v1",
            "fanout_finalizer": "fanout_finalizer_system@2026-08-role-aware-v1",
        }
        with tempfile.TemporaryDirectory() as tmp:
            rendered = {
                profile: RepositoryTurnSystemContextAssembler()
                .freeze_stable(_stable_request(tmp, profile=profile))
                .render()
                for profile in expected
            }

        for profile, marker in expected.items():
            self.assertIn(marker, rendered[profile])
            self.assertIn("actual authority", rendered[profile])
        self.assertEqual(len(set(rendered.values())), 3)

    def test_unknown_system_prompt_profile_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(KeyError, "unknown system prompt profile"):
                RepositoryTurnSystemContextAssembler().freeze_stable(
                    _stable_request(tmp, profile="unknown-role")
                )

    def test_stable_prefix_keeps_complete_system_and_ignores_large_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assembler = RepositoryTurnSystemContextAssembler()
            before = assembler.freeze_stable(_stable_request(tmp)).render()
            for index in range(50):
                (root / f"huge_{index}.py").write_text("x" * 10_000, encoding="utf-8")
            after = assembler.freeze_stable(_stable_request(tmp)).render()

        self.assertEqual(before, after)
        self.assertIn("system:\n", before)
        self.assertNotIn("... [middle truncated]", before.split("project_instructions:", 1)[0])

    def test_stable_budget_too_small_for_system_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "complete governing System Prompt"):
                RepositoryTurnSystemContextAssembler().freeze_stable(
                    _stable_request(tmp, max_chars=256)
                )

    def test_repo_map_does_not_ignore_workspace_because_of_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".agent_forge" / "runs" / "case" / "workspace"
            source = workspace / "src" / "module.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            repo_map = build_repo_map(workspace)
        self.assertEqual(repo_map, "src/module.py")

    def test_repo_map_reuses_content_edits_and_invalidates_after_structure_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            assembler = RepositoryTurnSystemContextAssembler()
            stable = assembler.freeze_stable(_stable_request(tmp)).render()
            request = _dynamic_request(tmp, stable)
            with patch(
                "agent_forge.runtime.adapters.context_assembler.build_repo_map",
                wraps=build_repo_map,
            ) as scan_repo_map:
                assembler.build(request)
                target.write_text("VALUE = 2\n", encoding="utf-8")
                assembler.build(request)
                self.assertEqual(scan_repo_map.call_count, 1)
                created = CreateFileTool(WorkspaceSandbox(root)).execute(
                    {"path": "new_module.py", "content": "NEW = 1\n"}
                )
                rebuilt = assembler.build(request)
                command = RunCommandTool(WorkspaceSandbox(root)).execute(
                    {"command": "python -m compileall target.py"}
                )
                assembler.build(request)

        self.assertTrue(created.success)
        self.assertTrue(command.success)
        self.assertEqual(scan_repo_map.call_count, 3)
        self.assertIn("new_module.py", rebuilt.repo_map)

    def test_dynamic_budget_cannot_squeeze_frozen_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("VALUE = 1\n" + "x" * 8_000, encoding="utf-8")
            (root / "FORGE.md").write_text("Always inspect target.py.\n", encoding="utf-8")
            assembler = RepositoryTurnSystemContextAssembler()
            stable = assembler.freeze_stable(_stable_request(tmp)).render()
            report = assembler.build(_dynamic_request(tmp, stable, max_chars=700))

        self.assertTrue(report.render().startswith(stable.rstrip()))
        self.assertEqual(report.stable_chars, len(stable))
        self.assertLessEqual(report.dynamic_chars, report.dynamic_max_chars)
        self.assertIn("target.py", report.selected_files)
        self.assertIn("Always inspect target.py", stable)


if __name__ == "__main__":
    unittest.main()
