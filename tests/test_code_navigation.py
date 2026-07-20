import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]

MAIN_ENTRY_MARKER = "# 主要入口："
RUNTIME_PORT_MARKER = "# 运行时端口："
CORE_RULE_MARKER = "# 核心规则："
CORE_DATA_MARKER = "# 核心数据："

# 第一遍只读这十个 owner。这里是学习地图，不是完整 Public API allowlist。
GOLDEN_PATH = {
    "agent_forge/harness.py": {
        "Harness.run": MAIN_ENTRY_MARKER,
    },
    "agent_forge/runtime/application/agent_loop.py": {
        "AgentLoop.run": MAIN_ENTRY_MARKER,
    },
    "agent_forge/runtime/application/run_preparation.py": {
        "RunPreparation.start": MAIN_ENTRY_MARKER,
        "RunPreparation.execute": MAIN_ENTRY_MARKER,
    },
    "agent_forge/runtime/application/turn_preparation.py": {
        "TurnPreparation.execute": MAIN_ENTRY_MARKER,
    },
    "agent_forge/context/application/compaction.py": {
        "ContextWindowManager.prepare": MAIN_ENTRY_MARKER,
    },
    "agent_forge/runtime/application/tool_execution.py": {
        "ToolExecutionPipeline.execute_calls": MAIN_ENTRY_MARKER,
    },
    "agent_forge/runtime/application/tool_authorization.py": {
        "ToolAuthorizationGate.authorize": MAIN_ENTRY_MARKER,
    },
    "agent_forge/runtime/application/operation_tracker.py": {
        "OperationTracker.describe": MAIN_ENTRY_MARKER,
        "OperationTracker.replay_if_executed": CORE_RULE_MARKER,
        "OperationTracker.record_result": RUNTIME_PORT_MARKER,
    },
    "agent_forge/runtime/application/run_lifecycle.py": {
        "RunLifecycle.update": RUNTIME_PORT_MARKER,
        "RunLifecycle.stop": RUNTIME_PORT_MARKER,
        "RunLifecycle.request_human_input": RUNTIME_PORT_MARKER,
    },
    "agent_forge/runtime/application/final_answer.py": {
        "FinalAnswerBuilder.execute": MAIN_ENTRY_MARKER,
    },
}

# 这些 owner 支撑黄金主链，但第一遍无需展开。
SUPPORTING_CORE_ENTRYPOINTS = {
    "agent_forge/runtime/application/run_control.py": ("ApplyRunControl.check",),
    "agent_forge/runtime/control.py": ("StepController.classify_observation",),
    "agent_forge/tools/tool_router.py": ("ToolRouter.route",),
    "agent_forge/observability/api.py": ("write_usage_artifacts",),
    "agent_forge/observability/application/usage.py": (
        "BuildUsageReport.execute",
    ),
}

# 这些能力保留面试深挖价值，但不属于 Single-Agent 五分钟主线。
ADVANCED_ENTRYPOINTS = {
    "agent_forge/hooks.py": (
        "RuntimeHook.before_model",
        "RuntimeHook.before_tool",
        "RuntimeHook.on_checkpoint",
        "RuntimeHook.on_stop",
    ),
    "agent_forge/context/api.py": ("propose_memory",),
    "agent_forge/multi_agent/application/coordinator.py": (
        "MultiAgentCoordinator.run",
    ),
    "agent_forge/multi_agent/application/live_fanout.py": (
        "LiveFanoutCoordinator.run",
    ),
    "agent_forge/bench/api.py": (
        "run_swebench",
        "run_benchmark_campaign",
    ),
    "agent_forge/evaluation/api.py": ("build_benchmark_scorecard",),
    "agent_forge/mcp/server.py": ("AgentForgeMCPServer.run",),
    "agent_forge/skills/registry.py": ("SkillRegistry.select_for_task",),
    "agent_forge/control.py": ("RunController.pause", "RunController.steer"),
}

# Adapter 可调用、可导航，但不得拥有 Runtime 状态与完成语义。
ADAPTER_ENTRYPOINTS = {
    "agent_forge/cli/parser.py": ("build_parser",),
    "agent_forge/cli/dispatch.py": ("main",),
    "agent_forge/cli/repository.py": ("run_repository_task",),
    "agent_forge/cli/resume.py": ("resume_repository_task",),
    "agent_forge/cli/operator.py": (
        "approve_request",
        "respond_to_human_input_request",
    ),
    "agent_forge/runtime/wiring.py": ("build_agent_loop",),
    "agent_forge/models/gateway.py": ("ModelGateway.chat",),
    "agent_forge/tools/registry.py": ("ToolRegistry.execute",),
    "agent_forge/bench/presentation/cli.py": ("run_swebench_from_args",),
}

# 只保留对理解状态、副作用与证据有帮助的具体 IO 边界。
ADAPTER_RUNTIME_BOUNDARIES = {
    "agent_forge/runtime/adapters/task_state_json.py": (
        "JsonTaskStateRepository.start",
        "JsonTaskStateRepository.update",
    ),
    "agent_forge/runtime/adapters/context_assembler.py": (
        "RepositoryContextAssembler.build",
    ),
    "agent_forge/runtime/adapters/human_input_json.py": (
        "JsonHumanInputRepository.request",
        "JsonHumanInputRepository.respond",
    ),
    "agent_forge/runtime/adapters/approval_json.py": (
        "JsonApprovalRepository.request",
        "JsonApprovalRepository.decide",
    ),
    "agent_forge/runtime/adapters/operation_ledger_json.py": (
        "JsonOperationLedgerRepository.ensure_planned",
        "JsonOperationLedgerRepository.record_executed",
        "JsonOperationLedgerRepository.record_failed",
    ),
    "agent_forge/observability/adapters/json_trace.py": (
        "JsonTraceRecorder.record_task_state_checkpoint",
    ),
    "agent_forge/safety/permission.py": ("PermissionPolicy.decide",),
    "agent_forge/safety/sandbox.py": ("WorkspaceSandbox.ensure_safe_path",),
    "agent_forge/safety/command_policy.py": ("check_command",),
}

DECISION_RULES = {
    "agent_forge/context/context_strategy.py": ("build_context_strategy",),
    "agent_forge/multi_agent/domain/fanout.py": (
        "build_execution_batches",
        "detect_write_scope_conflicts",
    ),
}

# 数据契约只覆盖黄金主链中的阶段边界；高级能力维护自己的局部导航。
GOLDEN_PATH_DATA = {
    "agent_forge/runtime/config.py": ("RuntimeConfig",),
    "agent_forge/runtime/application/dependencies.py": ("RuntimeDependencies",),
    "agent_forge/runtime/application/session.py": ("AgentRunSession",),
    "agent_forge/runtime/domain/task.py": (
        "TaskStartRequest",
        "TaskCheckpointUpdate",
        "TaskCheckpoint",
    ),
    "agent_forge/runtime/domain/conversation.py": (
        "Message",
        "ToolCall",
        "Observation",
        "AgentResponse",
    ),
    "agent_forge/runtime/domain/human_input.py": (
        "HumanInputQuestion",
        "HumanInputRequestDraft",
        "HumanInputRequest",
    ),
    "agent_forge/runtime/domain/operation.py": (
        "OperationTarget",
        "OperationPlan",
        "OperationTransition",
        "OperationRecord",
    ),
    "agent_forge/runtime/ports/context.py": ("ContextAssemblyRequest",),
    "agent_forge/context/application/compaction.py": (
        "PromptBudget",
        "ContextWindowRequest",
        "ContextWindowResult",
    ),
    "agent_forge/observability/domain/event.py": ("TraceEvent",),
}

# 显式继承仅用于无循环、无多继承冲突且能改善 IDE 导航的关系。
# ModelGateway 等已有基类的对象继续使用结构化类型，避免人为制造 MRO。
ADAPTER_PORT_RELATIONS = {
    "agent_forge/runtime/adapters/context_assembler.py": (
        "RepositoryContextAssembler",
        "ContextAssemblerPort",
    ),
    "agent_forge/runtime/adapters/task_state_json.py": (
        "JsonTaskStateRepository",
        "TaskStateRepository",
    ),
    "agent_forge/runtime/adapters/human_input_json.py": (
        "JsonHumanInputRepository",
        "HumanInputRepository",
    ),
    "agent_forge/runtime/adapters/approval_json.py": (
        "JsonApprovalRepository",
        "ApprovalRepository",
    ),
    "agent_forge/runtime/adapters/operation_ledger_json.py": (
        "JsonOperationLedgerRepository",
        "OperationLedgerRepository",
    ),
    "agent_forge/observability/adapters/json_trace.py": (
        "JsonTraceRecorder",
        "EventSink",
    ),
    "agent_forge/tools/registry.py": ("ToolRegistry", "ToolGateway"),
}


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.class_names: list[str] = []
        self.classes: dict[str, ast.ClassDef] = {}
        self.definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = ".".join([*self.class_names, node.name])
        self.classes[qualified_name] = node
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
    def test_golden_path_is_small_and_self_explaining(self) -> None:
        self._assert_navigation_contract(GOLDEN_PATH, require_docstring=True)

    def test_supporting_core_is_not_presented_as_the_golden_path(self) -> None:
        self._assert_markers(
            SUPPORTING_CORE_ENTRYPOINTS,
            MAIN_ENTRY_MARKER,
            require_docstring=True,
        )

    def test_advanced_entrypoints_remain_navigable_but_optional(self) -> None:
        self._assert_markers(
            ADVANCED_ENTRYPOINTS,
            MAIN_ENTRY_MARKER,
            require_docstring=True,
        )

    def test_adapter_entrypoints_are_classified_outside_runtime_owners(self) -> None:
        self._assert_markers(
            ADAPTER_ENTRYPOINTS,
            MAIN_ENTRY_MARKER,
            require_docstring=True,
        )

    def test_adapter_runtime_boundaries_remain_explicit(self) -> None:
        self._assert_markers(
            ADAPTER_RUNTIME_BOUNDARIES,
            RUNTIME_PORT_MARKER,
            require_docstring=False,
        )

    def test_decision_rules_are_visible_without_private_helpers(self) -> None:
        self._assert_markers(
            DECISION_RULES,
            CORE_RULE_MARKER,
            require_docstring=True,
        )

    def test_golden_path_data_is_distinct_from_process_entrypoints(self) -> None:
        self._assert_class_markers(GOLDEN_PATH_DATA, CORE_DATA_MARKER)

    def test_key_adapters_name_the_port_they_implement(self) -> None:
        for relative_path, (class_name, port_name) in ADAPTER_PORT_RELATIONS.items():
            path = PROJECT_ROOT / relative_path
            collector = _DefinitionCollector()
            collector.visit(ast.parse(path.read_text(encoding="utf-8")))
            with self.subTest(path=relative_path, adapter=class_name):
                node = collector.classes[class_name]
                base_names = {
                    base.id for base in node.bases if isinstance(base, ast.Name)
                }
                self.assertIn(
                    port_name,
                    base_names,
                    f"{relative_path}:{node.lineno} {class_name} must name {port_name}",
                )

    def test_agent_loop_entrypoint_only_exposes_the_phase_order(self) -> None:
        path = PROJECT_ROOT / "agent_forge/runtime/application/agent_loop.py"
        source = path.read_text(encoding="utf-8")
        collector = _DefinitionCollector()
        collector.visit(ast.parse(source))
        node = collector.definitions["AgentLoop.run"]
        self.assertIsNotNone(node.end_lineno)
        line_count = int(node.end_lineno or node.lineno) - node.lineno + 1
        self.assertLessEqual(line_count, 40)

        body = "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
        phase_calls = [
            "run_preparation.start",
            "run_preparation.execute",
            "_run_turn",
            "_stop",
        ]
        for name in phase_calls:
            self.assertIn(name, body)
        self.assertLess(
            body.index("run_preparation.start"),
            body.index("run_preparation.execute"),
        )
        self.assertLess(body.index("run_preparation.execute"), body.index("_run_turn"))

    def _assert_navigation_contract(
        self,
        expected: dict[str, dict[str, str]],
        *,
        require_docstring: bool,
    ) -> None:
        for relative_path, definitions in expected.items():
            for name, marker in definitions.items():
                self._assert_markers(
                    {relative_path: (name,)},
                    marker,
                    require_docstring=require_docstring,
                )

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
                    marker_line = self._preceding_marker(lines, node)
                    self.assertTrue(
                        marker_line.startswith(marker),
                        f"{relative_path}:{node.lineno} {name} "
                        f"must be preceded by {marker}",
                    )
                    self._assert_marker_is_specific(
                        marker_line,
                        marker,
                        relative_path,
                        node.lineno,
                        name,
                    )
                    if require_docstring:
                        self.assertTrue(
                            ast.get_docstring(node),
                            f"{relative_path}:{node.lineno} {name} "
                            "needs a navigation docstring",
                        )

    def _assert_class_markers(
        self,
        expected: dict[str, tuple[str, ...]],
        marker: str,
    ) -> None:
        for relative_path, names in expected.items():
            path = PROJECT_ROOT / relative_path
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            collector = _DefinitionCollector()
            collector.visit(ast.parse(source))
            for name in names:
                with self.subTest(path=relative_path, model=name):
                    self.assertIn(name, collector.classes)
                    node = collector.classes[name]
                    marker_line = self._preceding_marker(lines, node)
                    self.assertTrue(
                        marker_line.startswith(marker),
                        f"{relative_path}:{node.lineno} {name} "
                        f"must be preceded by {marker}",
                    )
                    self._assert_marker_is_specific(
                        marker_line,
                        marker,
                        relative_path,
                        node.lineno,
                        name,
                    )
                    self.assertTrue(
                        ast.get_docstring(node),
                        f"{relative_path}:{node.lineno} {name} "
                        "needs a data-contract docstring",
                    )

    @staticmethod
    def _preceding_marker(
        lines: list[str],
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> str:
        first_line = min(
            [
                node.lineno,
                *(decorator.lineno for decorator in node.decorator_list),
            ]
        )
        cursor = first_line - 2
        while cursor >= 0 and not lines[cursor].strip():
            cursor -= 1
        return lines[cursor].strip() if cursor >= 0 else ""

    def _assert_marker_is_specific(
        self,
        marker_line: str,
        marker: str,
        relative_path: str,
        first_line: int,
        name: str,
    ) -> None:
        description = marker_line.removeprefix(marker).strip()
        self.assertNotIn("下方定义", description)
        self.assertGreaterEqual(
            len(description),
            12,
            f"{relative_path}:{first_line} {name} 的导航说明过于空泛",
        )


if __name__ == "__main__":
    unittest.main()
