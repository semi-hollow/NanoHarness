"""Protect the small interview reading map without testing prose wording."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from agent_forge.control import RunController
from agent_forge.runtime.adapters.run_control_noop import NoopRunControl
from agent_forge.runtime.hooks import HookManager
from agent_forge.runtime.ports import HookPort, RunControlPort


PROJECT_ROOT = Path(__file__).parents[1]

# This is the canonical first-pass Runtime reading budget. Evaluation has its
# own follow-up map and must not displace Runtime state owners from this list.
RUNTIME_CORE = {
    "agent_forge/harness.py": "Harness.run",
    "agent_forge/runtime/wiring.py": "build_agent_loop_from_request",
    "agent_forge/runtime/application/agent_loop.py": "AgentLoop.run",
    "agent_forge/runtime/application/session.py": "AgentRunSession",
    "agent_forge/runtime/application/turn_preparation.py": "TurnPreparation.execute",
    "agent_forge/runtime/application/tool_execution.py": "ToolExecutionPipeline.execute_calls",
    "agent_forge/runtime/application/operation_tracker.py": "OperationTracker.describe",
    "agent_forge/runtime/application/run_lifecycle.py": "RunLifecycle.update",
    "agent_forge/runtime/domain/task.py": "TaskCheckpoint.apply_transition",
    "agent_forge/runtime/domain/operation.py": "OperationRecord.transition",
    "agent_forge/observability/domain/event.py": "TraceEvent",
    "agent_forge/observability/domain/run_story.py": "RunStory",
}


class CodeNavigationContractTest(unittest.TestCase):
    def test_runtime_core_is_exactly_twelve_existing_files(self) -> None:
        self.assertEqual(len(RUNTIME_CORE), 12)
        for relative_path in RUNTIME_CORE:
            with self.subTest(path=relative_path):
                self.assertTrue((PROJECT_ROOT / relative_path).is_file())

    def test_runtime_core_has_module_and_owner_docstrings(self) -> None:
        for relative_path, owner in RUNTIME_CORE.items():
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source)
            with self.subTest(path=relative_path, owner=owner):
                self.assertTrue(ast.get_docstring(tree), "core module needs a navigation docstring")
                node = _find_owner(tree, owner)
                self.assertIsNotNone(node, f"missing canonical owner: {owner}")
                self.assertTrue(ast.get_docstring(node), f"{owner} needs a concise owner docstring")

    def test_control_adapters_explicitly_expose_their_port_hierarchy(self) -> None:
        """关键控制面牺牲一点结构化自由，换取 PyCharm 可直接导航实现。"""

        self.assertIn(HookPort, HookManager.__bases__)
        self.assertIn(RunControlPort, RunController.__bases__)
        self.assertIn(RunControlPort, NoopRunControl.__bases__)

    def test_tool_execution_has_no_orphan_private_methods(self) -> None:
        path = PROJECT_ROOT / "agent_forge/runtime/application/tool_execution.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _find_named(tree.body, "ToolExecutionPipeline")
        self.assertIsInstance(owner, ast.ClassDef)
        assert isinstance(owner, ast.ClassDef)

        private_methods = {
            node.name
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_")
            and not node.name.startswith("__")
        }
        self_calls = {
            node.func.attr
            for node in ast.walk(owner)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        }
        self.assertEqual(private_methods - self_calls, set())


def _find_owner(
    tree: ast.Module,
    owner: str,
) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None:
    parts = owner.split(".")
    if len(parts) == 1:
        return _find_named(tree.body, parts[0])
    parent = _find_named(tree.body, parts[0])
    if not isinstance(parent, ast.ClassDef):
        return None
    return _find_named(parent.body, parts[1])


def _find_named(
    nodes: list[ast.stmt],
    name: str,
) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in nodes:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


if __name__ == "__main__":
    unittest.main()
