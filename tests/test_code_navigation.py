"""保护精简的代码阅读地图与关键入口命名。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from agent_forge.control import RunController
from agent_forge.runtime.adapters.run_control_noop import NoopRunControl
from agent_forge.runtime.hooks import HookManager
from agent_forge.runtime.ports import HookPort, RunControlPort


PROJECT_ROOT = Path(__file__).parents[1]

# 这是首遍阅读 Runtime 的固定预算；Evaluation 的后续地图不能挤掉状态 owner。
RUNTIME_CORE = {
    "agent_forge/harness.py": "Harness.run",
    "agent_forge/runtime/wiring.py": "build_agent_loop_from_request",
    "agent_forge/runtime/application/agent_loop.py": "AgentLoop.run",
    "agent_forge/runtime/application/session.py": "AgentRunSession",
    "agent_forge/runtime/application/turn_preparation.py": "TurnPreparation.prepare_turn",
    "agent_forge/runtime/application/tool_execution.py": "ToolExecutionPipeline.execute_calls",
    "agent_forge/runtime/application/operation_tracker.py": "OperationTracker.build_operation_intent",
    "agent_forge/runtime/application/run_lifecycle.py": "RunLifecycle.update_checkpoint",
    "agent_forge/runtime/domain/task.py": "TaskCheckpoint.apply_transition",
    "agent_forge/runtime/domain/operation.py": "OperationRecord.transition",
    "agent_forge/observability/domain/event.py": "TraceEvent",
    "agent_forge/observability/domain/run_story.py": "RunStory",
}

# 这些类是阅读和运行主链会直接遇到的应用服务。它们的公开方法名必须单独
# 表达业务动作，不能退化为脱离类名后没有信息量的通用动词。
CRITICAL_APPLICATION_SERVICES = {
    "agent_forge/runtime/application/run_preparation.py": {
        "RunPreparation",
    },
    "agent_forge/runtime/application/turn_preparation.py": {
        "TurnPreparation",
    },
    "agent_forge/runtime/application/run_lifecycle.py": {
        "RunLifecycle",
    },
    "agent_forge/runtime/application/run_control.py": {
        "RunControlHandler",
    },
    "agent_forge/runtime/application/final_answer.py": {
        "FinalAnswerBuilder",
    },
    "agent_forge/runtime/application/tool_feedback.py": {
        "ToolFeedback",
    },
    "agent_forge/runtime/application/operation_tracker.py": {
        "OperationTracker",
    },
    "agent_forge/runtime/application/tool_authorization.py": {
        "ToolAuthorizationGate",
    },
    "agent_forge/runtime/application/operator_control.py": {
        "DecideApproval",
        "RespondToHumanInput",
        "BuildContinuationPlan",
    },
    "agent_forge/bench/application/swebench.py": {
        "RunSwebench",
    },
    "agent_forge/bench/application/campaign.py": {
        "RunBenchmarkCampaign",
    },
    "agent_forge/bench/application/case_inspection.py": {
        "InspectBenchCase",
    },
    "agent_forge/evaluation/application/scorecard.py": {
        "BuildBenchmarkScorecard",
    },
    "agent_forge/observability/application/usage.py": {
        "BuildUsageReport",
    },
}
VAGUE_ENTRYPOINT_NAMES = {
    "append",
    "check",
    "describe",
    "execute",
    "handle",
    "process",
    "start",
    "stop",
    "update",
}
REMOVED_PATCH_API_MARKERS = {
    '"apply_patch"',
    "'apply_patch'",
    "patch.diff",
    "integration.patch",
    "ApplyPatchTool",
    "CandidatePatchPort",
    "GitCandidatePatch",
    "write_integration_patch",
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
                self.assertTrue(
                    ast.get_docstring(tree), "core module needs a navigation docstring"
                )
                node = _find_owner(tree, owner)
                self.assertIsNotNone(node, f"missing canonical owner: {owner}")
                self.assertTrue(
                    ast.get_docstring(node), f"{owner} needs a concise owner docstring"
                )

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

    def test_critical_application_services_do_not_expose_vague_verbs(self) -> None:
        """关键调用点应直接说明动作，不要求读者先记住所属类。"""

        for relative_path, class_names in CRITICAL_APPLICATION_SERVICES.items():
            tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
            for class_name in class_names:
                owner = _find_named(tree.body, class_name)
                self.assertIsInstance(owner, ast.ClassDef)
                assert isinstance(owner, ast.ClassDef)
                public_method_names = {
                    node.name
                    for node in owner.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not node.name.startswith("_")
                }
                with self.subTest(path=relative_path, owner=class_name):
                    self.assertEqual(
                        public_method_names & VAGUE_ENTRYPOINT_NAMES,
                        set(),
                    )

    def test_runtime_source_keeps_edit_actions_distinct_from_diff_artifacts(
        self,
    ) -> None:
        """旧 patch API 不应重新进入生产代码；外部 ``model_patch`` 不受影响。"""

        for path in (PROJECT_ROOT / "agent_forge").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertEqual(
                    {
                        marker
                        for marker in REMOVED_PATCH_API_MARKERS
                        if marker in source
                    },
                    set(),
                )


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
