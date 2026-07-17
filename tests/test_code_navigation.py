import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]

PRIMARY_ENTRYPOINTS = {
    "agent_forge/cli/parser.py": ("build_parser",),
    "agent_forge/cli/dispatch.py": ("main",),
    "agent_forge/cli/repository.py": ("run_repository_task",),
    "agent_forge/cli/resume.py": ("resume_repository_task",),
    "agent_forge/cli/operator.py": (
        "approve_request",
        "respond_to_human_input_request",
    ),
    "agent_forge/runtime/application/agent_loop.py": ("AgentLoop.run",),
    "agent_forge/runtime/application/run_preparation.py": (
        "RunPreparation.start",
        "RunPreparation.execute",
    ),
    "agent_forge/runtime/application/turn_preparation.py": ("TurnPreparation.execute",),
    "agent_forge/runtime/application/final_answer.py": ("FinalAnswerBuilder.execute",),
    "agent_forge/runtime/application/tool_authorization.py": (
        "ToolAuthorizationGate.authorize",
    ),
    "agent_forge/runtime/application/tool_execution.py": (
        "ToolExecutionPipeline.execute_calls",
    ),
    "agent_forge/runtime/application/operation_tracker.py": (
        "OperationTracker.describe",
    ),
    "agent_forge/runtime/application/operator_control.py": (
        "DecideApproval.execute",
        "RespondToHumanInput.execute",
        "BuildContinuationPlan.execute",
    ),
    "agent_forge/runtime/wiring.py": ("build_agent_loop",),
    "agent_forge/runtime/execution_environment.py": ("ExecutionEnvironment.prepare",),
    "agent_forge/runtime/control.py": ("StepController.classify_observation",),
    "agent_forge/runtime/structured_output.py": ("StructuredOutputParser.parse",),
    "agent_forge/context/api.py": (
        "propose_memory",
        "promote_memory",
        "retire_memory",
        "reject_memory",
        "list_memories",
    ),
    "agent_forge/context/application/memory_service.py": (
        "LongTermMemoryService.propose",
        "LongTermMemoryService.promote",
        "LongTermMemoryService.recall",
        "LongTermMemoryService.retire",
        "LongTermMemoryService.reject",
    ),
    "agent_forge/context/application/compaction.py": ("ContextWindowManager.prepare",),
    "agent_forge/context/context_builder.py": ("build_context_report",),
    "agent_forge/models/gateway.py": ("ModelGateway.chat",),
    "agent_forge/tools/tool_router.py": ("ToolRouter.route",),
    "agent_forge/tools/registry.py": ("ToolRegistry.execute",),
    "agent_forge/tools/mcp_config.py": ("MCPConfigLoader.load_into",),
    "agent_forge/multi_agent/application/coordinator.py": (
        "MultiAgentCoordinator.run",
    ),
    "agent_forge/multi_agent/application/fanout.py": ("run_fanout",),
    "agent_forge/multi_agent/application/live_fanout.py": (
        "LiveFanoutCoordinator.run",
    ),
    "agent_forge/multi_agent/wiring.py": (
        "build_live_fanout",
        "build_multi_agent_coordinator",
    ),
    "agent_forge/bench/api.py": (
        "run_swebench",
        "inspect_swebench_case",
        "list_regression_case_profiles",
        "get_regression_set_profile",
    ),
    "agent_forge/bench/application/case_inspection.py": ("InspectBenchCase.execute",),
    "agent_forge/bench/application/swebench.py": ("RunSwebench.execute",),
    "agent_forge/bench/application/diagnostics.py": ("DiagnoseBenchCase.attach",),
    "agent_forge/bench/domain/failure_taxonomy.py": ("classify_case_result",),
    "agent_forge/bench/adapters/case_runtime.py": ("LocalCaseExecutor.run",),
    "agent_forge/bench/adapters/official_results.py": ("parse_official_results",),
    "agent_forge/bench/presentation/case_inspection.py": (
        "render_case_catalog",
        "render_case_inspection",
    ),
    "agent_forge/bench/presentation/cli.py": (
        "run_swebench_from_args",
        "render_case_catalog_from_args",
        "render_case_inspection_from_args",
    ),
    "agent_forge/bench/presentation/case_study.py": ("write_case_study",),
    "agent_forge/bench/presentation/report.py": ("write_bench_artifacts",),
    "agent_forge/evaluation/api.py": ("build_benchmark_scorecard",),
    "agent_forge/evaluation/application/scorecard.py": (
        "BuildBenchmarkScorecard.execute",
    ),
    "agent_forge/evaluation/domain/comparison.py": ("compare_runs", "compare_variants"),
    "agent_forge/evaluation/domain/ablation.py": ("compare_benchmark_scorecards",),
    "agent_forge/evaluation/adapters/feedback_dataset_files.py": (
        "record_feedback",
        "export_feedback_dataset",
    ),
    "agent_forge/observability/api.py": ("write_usage_artifacts",),
    "agent_forge/observability/application/usage.py": ("BuildUsageReport.execute",),
    "agent_forge/mcp/server.py": ("AgentForgeMCPServer.run",),
    "agent_forge/skills/registry.py": ("SkillRegistry.select_for_task",),
    "agent_forge/workbench/presentation/http.py": ("run_ui",),
    "agent_forge/workbench/presentation/commands.py": ("build_workbench_command",),
    "agent_forge/showcase/control_plane.py": (
        "start_control_plane_showcase",
        "continue_control_plane_showcase",
    ),
}

RUNTIME_PORTS = {
    "agent_forge/cli/repository.py": ("prepare_execution_environment",),
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
        "JsonOperationLedgerRepository.record_executed",
        "JsonOperationLedgerRepository.record_failed",
        "JsonOperationLedgerRepository.ensure_planned",
    ),
    "agent_forge/runtime/hooks.py": ("HookManager.pre_tool",),
    "agent_forge/runtime/application/run_lifecycle.py": (
        "RunLifecycle.update",
        "RunLifecycle.stop",
        "RunLifecycle.request_human_input",
    ),
    "agent_forge/safety/permission.py": ("PermissionPolicy.decide",),
    "agent_forge/safety/sandbox.py": ("WorkspaceSandbox.ensure_safe_path",),
    "agent_forge/safety/command_policy.py": ("check_command",),
    "agent_forge/observability/adapters/json_trace.py": (
        "JsonTraceRecorder.record_task_state_checkpoint",
        "JsonTraceRecorder.write",
    ),
    "agent_forge/bench/adapters/official_results.py": ("apply_official_results",),
    "agent_forge/context/adapters/memory_json.py": (
        "JsonLongTermMemoryRepository.save",
        "JsonLongTermMemoryRepository.get",
        "JsonLongTermMemoryRepository.list_records",
    ),
}

CORE_RULES = {
    "agent_forge/context/context_strategy.py": ("build_context_strategy",),
    "agent_forge/multi_agent/domain/fanout.py": (
        "build_execution_batches",
        "build_conflict_free_batches",
        "detect_write_scope_conflicts",
        "detect_result_conflicts",
    ),
}

CORE_DATA_MODELS = {
    "agent_forge/runtime/config.py": ("RuntimeConfig",),
    "agent_forge/runtime/application/dependencies.py": ("RuntimeDependencies",),
    "agent_forge/runtime/application/session.py": ("AgentRunSession",),
    "agent_forge/runtime/domain/task.py": (
        "TaskStartRequest",
        "TaskCheckpointUpdate",
        "TaskCheckpoint",
    ),
    "agent_forge/runtime/domain/approval.py": (
        "ApprovalRequestDraft",
        "ApprovalRequest",
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
    "agent_forge/runtime/domain/conversation.py": (
        "Message",
        "ToolCall",
        "Observation",
        "AgentResponse",
    ),
    "agent_forge/runtime/llm_config.py": ("LLMConfig", "LLMConfigRequest"),
    "agent_forge/runtime/ports/context.py": ("ContextAssemblyRequest",),
    "agent_forge/runtime/wiring.py": (
        "ToolRegistryBuildRequest",
        "HumanInputResponseCommand",
    ),
    "agent_forge/runtime/application/working_memory.py": ("WorkingMemory",),
    "agent_forge/context/context_strategy.py": ("ContextStrategy",),
    "agent_forge/context/api.py": ("ProposeMemoryRequest",),
    "agent_forge/context/context_builder.py": (
        "ContextBuildPolicy",
        "ContextBuildRequest",
        "ContextBuildReport",
    ),
    "agent_forge/context/domain/memory.py": (
        "LongTermMemoryRecord",
        "MemoryProposal",
        "SessionDigest",
    ),
    "agent_forge/context/application/compaction.py": (
        "PromptBudget",
        "ContextWindowRequest",
        "ContextWindowResult",
    ),
    "agent_forge/models/gateway.py": ("RetryPolicy",),
    "agent_forge/tools/tool_router.py": ("ToolRoutingRequest", "ToolRoute"),
    "agent_forge/multi_agent/wiring.py": (
        "LiveFanoutBuildRequest",
        "SequentialCoordinatorBuildRequest",
    ),
    "agent_forge/multi_agent/domain/fanout.py": (
        "SubagentTask",
        "FanoutConflict",
        "SubagentResult",
        "FanoutResult",
    ),
    "agent_forge/multi_agent/domain/live.py": (
        "FanoutPlan",
        "FanoutCheckpoint",
        "LiveSubagentResult",
        "LiveFanoutSummary",
    ),
    "agent_forge/multi_agent/domain/models.py": (
        "RoleSpec",
        "AgentProfile",
        "Artifact",
        "RoleRunResult",
        "MultiAgentRunSummary",
    ),
    "agent_forge/bench/domain/config.py": ("SwebenchRunRequest", "BenchRunLayout"),
    "agent_forge/bench/domain/models.py": (
        "BenchCase",
        "BenchCaseResult",
        "BenchRunSummary",
    ),
    "agent_forge/bench/domain/case_inspection.py": (
        "BenchmarkCaseProfile",
        "BenchmarkSetProfile",
        "PatchSummary",
        "BenchmarkCaseInspection",
    ),
    "agent_forge/bench/domain/failure_taxonomy.py": ("FailureDiagnosis",),
    "agent_forge/bench/adapters/official_results.py": (
        "OfficialCaseOutcome",
        "OfficialResults",
    ),
    "agent_forge/evaluation/domain/models.py": ("EvaluationComparison",),
    "agent_forge/evaluation/domain/ablation.py": ("AblationComparisonRequest",),
    "agent_forge/evaluation/api.py": ("AblationArtifactRequest",),
    "agent_forge/evaluation/adapters/feedback_dataset_files.py": ("FeedbackRequest",),
    "agent_forge/observability/domain/event.py": ("TraceEvent",),
    "agent_forge/observability/domain/evidence.py": ("EvidenceItem", "EvidenceLedger"),
}

# 这些接口是高频事件或局部解析原语；拆成请求对象会隐藏调用语义。
LONG_PARAMETER_EXCEPTIONS = {
    (
        "agent_forge/multi_agent/ports/sequential.py",
        "CoordinatorEventSink.record_event",
    ),
    ("agent_forge/observability/adapters/json_trace.py", "JsonTraceRecorder.add"),
    (
        "agent_forge/observability/adapters/json_trace.py",
        "JsonTraceRecorder.record_event",
    ),
    (
        "agent_forge/runtime/application/operation_tracker.py",
        "OperationTracker.record_result",
    ),
    (
        "agent_forge/runtime/application/tool_authorization.py",
        "ToolAuthorizationGate.post_process",
    ),
    ("agent_forge/runtime/ports/events.py", "EventSink.add"),
    ("agent_forge/workbench/presentation/commands.py", "payload_int"),
    ("agent_forge/workbench/presentation/commands.py", "payload_float"),
}

FIELD_DOCUMENTED_MODELS = {
    "agent_forge/context/domain/memory.py": (
        "LongTermMemoryRecord",
        "SessionDigest",
    ),
    "agent_forge/bench/domain/case_inspection.py": (
        "BenchmarkCaseProfile",
        "BenchmarkSetProfile",
        "PatchSummary",
        "BenchmarkCaseInspection",
    ),
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
    def test_primary_entrypoints_are_visible_when_bodies_are_collapsed(self) -> None:
        self._assert_markers(
            PRIMARY_ENTRYPOINTS, "# 主要入口：", require_docstring=True
        )

    def test_runtime_ports_are_visible_when_bodies_are_collapsed(self) -> None:
        self._assert_markers(RUNTIME_PORTS, "# 运行时端口：", require_docstring=False)

    def test_core_rules_are_visible_without_reading_private_helpers(self) -> None:
        self._assert_markers(CORE_RULES, "# 核心规则：", require_docstring=True)

    def test_core_data_models_are_distinct_from_process_entrypoints(self) -> None:
        self._assert_class_markers(CORE_DATA_MODELS, "# 核心数据：")

    def test_memory_and_benchmark_models_explain_every_field(self) -> None:
        for relative_path, names in FIELD_DOCUMENTED_MODELS.items():
            path = PROJECT_ROOT / relative_path
            collector = _DefinitionCollector()
            collector.visit(ast.parse(path.read_text(encoding="utf-8")))
            for name in names:
                with self.subTest(path=relative_path, model=name):
                    node = collector.classes[name]
                    docstring = ast.get_docstring(node) or ""
                    fields = [
                        statement.target.id
                        for statement in node.body
                        if isinstance(statement, ast.AnnAssign)
                        and isinstance(statement.target, ast.Name)
                    ]
                    for field in fields:
                        self.assertIn(
                            f"``{field}``",
                            docstring,
                            f"{relative_path}:{node.lineno} {name}.{field} 缺少字段说明",
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

    def test_public_boundaries_do_not_regrow_long_parameter_lists(self) -> None:
        """Use named request objects once a public call needs five business inputs."""

        violations: list[str] = []
        observed_exceptions: set[tuple[str, str]] = set()
        package_root = PROJECT_ROOT / "agent_forge"
        for path in sorted(package_root.rglob("*.py")):
            relative_path = str(path.relative_to(PROJECT_ROOT))
            collector = _DefinitionCollector()
            collector.visit(ast.parse(path.read_text(encoding="utf-8")))
            for name, node in collector.definitions.items():
                if node.name.startswith("_") or node.name == "__init__":
                    continue
                parameters = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                parameters = [
                    item for item in parameters if item.arg not in {"self", "cls"}
                ]
                if len(parameters) < 5:
                    continue
                identity = (relative_path, name)
                if identity in LONG_PARAMETER_EXCEPTIONS:
                    observed_exceptions.add(identity)
                    continue
                violations.append(
                    f"{relative_path}:{node.lineno} {name} has {len(parameters)} parameters"
                )

        self.assertEqual(violations, [], "Use a typed request object at this boundary")
        self.assertEqual(
            observed_exceptions,
            LONG_PARAMETER_EXCEPTIONS,
            "Remove stale exceptions when a local API becomes smaller",
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
                    first_line = min(
                        [
                            node.lineno,
                            *(decorator.lineno for decorator in node.decorator_list),
                        ]
                    )
                    cursor = first_line - 2
                    while cursor >= 0 and not lines[cursor].strip():
                        cursor -= 1
                    self.assertGreaterEqual(cursor, 0)
                    self.assertTrue(
                        lines[cursor].strip().startswith(marker),
                        f"{relative_path}:{first_line} {name} must be preceded by {marker}",
                    )
                    self._assert_marker_is_specific(
                        lines[cursor].strip(),
                        marker,
                        relative_path,
                        first_line,
                        name,
                    )
                    if require_docstring:
                        self.assertTrue(
                            ast.get_docstring(node),
                            f"{relative_path}:{first_line} {name} needs a navigation docstring",
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
                    first_line = min(
                        [
                            node.lineno,
                            *(decorator.lineno for decorator in node.decorator_list),
                        ]
                    )
                    cursor = first_line - 2
                    while cursor >= 0 and not lines[cursor].strip():
                        cursor -= 1
                    self.assertGreaterEqual(cursor, 0)
                    marker_line = lines[cursor].strip()
                    self.assertTrue(
                        marker_line.startswith(marker),
                        f"{relative_path}:{first_line} {name} must be preceded by {marker}",
                    )
                    self._assert_marker_is_specific(
                        marker_line,
                        marker,
                        relative_path,
                        first_line,
                        name,
                    )
                    self.assertTrue(
                        ast.get_docstring(node),
                        f"{relative_path}:{first_line} {name} needs a data-contract docstring",
                    )

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
