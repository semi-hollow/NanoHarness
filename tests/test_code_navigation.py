import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]

PRIMARY_ENTRYPOINTS = {
    "agent_forge/forge_cli.py": (
        "main",
        "run_repository_task",
        "resume_repository_task",
        "approve_request",
        "respond_to_human_input",
    ),
    "agent_forge/runtime/agent_loop.py": ("AgentLoop.run",),
    "agent_forge/runtime/execution_environment.py": ("ExecutionEnvironment.prepare",),
    "agent_forge/runtime/control.py": ("StepController.classify_observation",),
    "agent_forge/runtime/structured_output.py": ("StructuredOutputParser.parse",),
    "agent_forge/context/context_builder.py": ("build_context_report",),
    "agent_forge/models/gateway.py": ("ModelGateway.chat",),
    "agent_forge/tools/tool_router.py": ("ToolRouter.route",),
    "agent_forge/tools/registry.py": ("ToolRegistry.execute",),
    "agent_forge/tools/mcp_config.py": ("MCPConfigLoader.load_into",),
    "agent_forge/multi_agent/coordinator.py": ("MultiAgentCoordinator.run",),
    "agent_forge/multi_agent/live_fanout.py": ("LiveFanoutCoordinator.run",),
    "agent_forge/bench/swebench.py": ("run_swebench",),
    "agent_forge/bench/diagnostics.py": ("attach_failure_diagnosis",),
    "agent_forge/bench/official_results.py": ("parse_official_results",),
    "agent_forge/bench/case_study.py": ("write_case_study",),
    "agent_forge/bench/report.py": ("write_bench_artifacts",),
    "agent_forge/evaluation/comparison.py": ("compare_runs", "compare_variants"),
    "agent_forge/evaluation/scorecard.py": ("build_benchmark_scorecard",),
    "agent_forge/evaluation/experiment.py": ("compare_benchmark_scorecards",),
    "agent_forge/evaluation/feedback_dataset.py": ("record_feedback", "export_feedback_dataset"),
    "agent_forge/mcp/server.py": ("AgentForgeMCPServer.run",),
    "agent_forge/skills/registry.py": ("SkillRegistry.select_for_task",),
    "agent_forge/ui.py": ("run_ui",),
}

RUNTIME_PORTS = {
    "agent_forge/forge_cli.py": ("prepare_execution_environment",),
    "agent_forge/runtime/task_state.py": ("TaskStateStore.start", "TaskStateStore.update"),
    "agent_forge/runtime/human_input.py": ("HumanInputStore.request", "HumanInputStore.respond"),
    "agent_forge/runtime/approval.py": ("ApprovalStore.request", "ApprovalStore.decide"),
    "agent_forge/runtime/operation_ledger.py": (
        "OperationLedgerStore.record_executed",
        "OperationLedgerStore.record_failed",
        "OperationLedgerStore.ensure_planned",
    ),
    "agent_forge/runtime/hooks.py": ("HookManager.pre_tool",),
    "agent_forge/runtime/run_lifecycle.py": (
        "RunLifecycle.update",
        "RunLifecycle.stop",
        "RunLifecycle.request_human_input",
    ),
    "agent_forge/runtime/tool_execution.py": ("ToolExecutionPipeline.execute_calls",),
    "agent_forge/safety/permission.py": ("PermissionPolicy.decide",),
    "agent_forge/safety/sandbox.py": ("WorkspaceSandbox.ensure_safe_path",),
    "agent_forge/safety/command_policy.py": ("check_command",),
    "agent_forge/observability/trace.py": (
        "TraceRecorder.record_task_state_checkpoint",
        "TraceRecorder.write",
    ),
    "agent_forge/bench/official_results.py": ("apply_official_results",),
}


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.class_names: list[str] = []
        self.definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_names.append(node.name)
        self.generic_visit(node)
        self.class_names.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node)
        self.generic_visit(node)

    def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified_name = ".".join([*self.class_names, node.name])
        self.definitions[qualified_name] = node


class CodeNavigationContractTest(unittest.TestCase):
    def test_primary_entrypoints_are_visible_when_bodies_are_collapsed(self) -> None:
        self._assert_markers(PRIMARY_ENTRYPOINTS, "# PRIMARY ENTRYPOINT:", require_docstring=True)

    def test_runtime_ports_are_visible_when_bodies_are_collapsed(self) -> None:
        self._assert_markers(RUNTIME_PORTS, "# RUNTIME PORT:", require_docstring=False)

    def test_agent_loop_entrypoint_only_exposes_the_phase_order(self) -> None:
        path = PROJECT_ROOT / "agent_forge/runtime/agent_loop.py"
        source = path.read_text(encoding="utf-8")
        collector = _DefinitionCollector()
        collector.visit(ast.parse(source))
        node = collector.definitions["AgentLoop.run"]
        self.assertIsNotNone(node.end_lineno)
        line_count = int(node.end_lineno or node.lineno) - node.lineno + 1
        self.assertLessEqual(line_count, 40)

        body = "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
        phase_calls = ["_start_session", "_prepare_run", "_run_turn", "_stop"]
        for name in phase_calls:
            self.assertIn(name, body)
        self.assertLess(body.index("_start_session"), body.index("_prepare_run"))
        self.assertLess(body.index("_prepare_run"), body.index("_run_turn"))

    def _assert_markers(
        self,
        expected: dict[str, tuple[str, ...]],
        marker: str,
        *,
        require_docstring: bool,
    ) -> None:
        for relative_path, names in expected.items():
            path = PROJECT_ROOT / relative_path
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            collector = _DefinitionCollector()
            collector.visit(ast.parse(source))
            for name in names:
                with self.subTest(path=relative_path, definition=name):
                    self.assertIn(name, collector.definitions)
                    node = collector.definitions[name]
                    first_line = min(
                        [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                    )
                    cursor = first_line - 2
                    while cursor >= 0 and not lines[cursor].strip():
                        cursor -= 1
                    self.assertGreaterEqual(cursor, 0)
                    self.assertTrue(
                        lines[cursor].strip().startswith(marker),
                        f"{relative_path}:{first_line} {name} must be preceded by {marker}",
                    )
                    if require_docstring:
                        self.assertTrue(
                            ast.get_docstring(node),
                            f"{relative_path}:{first_line} {name} needs a navigation docstring",
                        )


if __name__ == "__main__":
    unittest.main()
