"""保护精简的代码阅读地图与关键入口命名。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from agent_forge.control import RunController
from agent_forge.runtime.adapters.run_control_noop import NoopRunControl
from agent_forge.runtime.adapters.hook_manager import HookManager
from agent_forge.runtime.ports import HookPort, RunControlPort


PROJECT_ROOT = Path(__file__).parents[1]

# 这是架构导航必须保留的 Runtime owner 集合；五个主入口负责提供整体控制流，
# 其余 owner 按四层工具治理结构承载具体机制。
RUNTIME_CORE = {
    "agent_forge/harness.py": "Harness.run",
    "agent_forge/runtime/wiring.py": "build_agent_loop_from_request",
    "agent_forge/runtime/application/agent_loop.py": "AgentLoop.run",
    "agent_forge/runtime/application/session.py": "AgentRunSession",
    "agent_forge/runtime/application/model_step_preparation.py": "ModelStepPreparation.prepare_model_step",
    "agent_forge/runtime/application/tool_execution.py": "ToolExecutionPipeline.execute_calls",
    "agent_forge/runtime/application/operation_tracker.py": "OperationTracker.build_operation_intent",
    "agent_forge/runtime/application/run_lifecycle.py": "RunLifecycle.update_checkpoint",
    "agent_forge/runtime/domain/task.py": "TaskCheckpoint.apply_transition",
    "agent_forge/runtime/domain/operation.py": "OperationRecord.transition",
    "agent_forge/observability/domain/event.py": "TraceEvent",
    "agent_forge/observability/domain/run_story.py": "RunStory",
}

# 这些是演示和排障会直接进入的长主链。除 docstring 外，它们必须切成
# 可折叠阶段，使 Collapse All 后呈现架构骨架；普通 serializer/renderer 不在此
# 机械加注释，避免注释本身变成新噪音。
CORE_WORKFLOW_ENTRYPOINTS = {
    "agent_forge/harness.py": {
        "Harness.run": 3,
        "Harness._execute_run": 3,
    },
    "agent_forge/runtime/application/agent_loop.py": {
        "AgentLoop.run": 3,
        "AgentLoop._run_model_step": 4,
    },
    "agent_forge/runtime/application/model_step_preparation.py": {
        "ModelStepPreparation.prepare_model_step": 4,
    },
    "agent_forge/runtime/application/tool_execution.py": {
        "ToolExecutionPipeline.execute_calls": 3,
        "ToolExecutionPipeline._execute_call": 4,
        "ToolExecutionPipeline._handle_human_question": 3,
        "ToolExecutionPipeline._run_tool": 4,
    },
    "agent_forge/runtime/application/tool_authorization.py": {
        "ToolAuthorizationGate.authorize": 2,
        "ToolAuthorizationGate._resolve_approval": 4,
    },
    "agent_forge/runtime/application/run_lifecycle.py": {
        "RunLifecycle.finalize_run": 3,
        "RunLifecycle.request_human_input": 3,
    },
    "agent_forge/runtime/application/run_control.py": {
        "RunControlHandler.consume_pending_signals": 3,
    },
    "agent_forge/context/application/compaction.py": {
        "PromptWindowManager.prepare": 4,
    },
    "agent_forge/memory/application/service.py": {
        "LongTermMemoryService.apply_consolidation": 3,
        "LongTermMemoryService.forget": 2,
        "LongTermMemoryService.list_for_project": 2,
        "LongTermMemoryService.recall": 3,
    },
    "agent_forge/tools/tool_router.py": {
        "ToolRouter.route": 4,
    },
    "agent_forge/multi_agent/application/fanout.py": {
        "FanoutCoordinator.run": 3,
        "FanoutCoordinator._run_plan": 3,
        "FanoutCoordinator._integrate_result": 3,
        "FanoutCoordinator._run_worker_attempt": 3,
        "FanoutCoordinator._replan_remaining": 3,
        "FanoutCoordinator._restore_previous": 4,
    },
    "agent_forge/multi_agent/adapters/local_worker.py": {
        "LocalAgentWorkerAdapter.run_worker": 5,
        "LocalAgentWorkerAdapter.run_finalizer": 4,
    },
    "agent_forge/multi_agent/domain/live.py": {
        "aggregate_live_metrics": 3,
    },
    "agent_forge/bench/application/swebench.py": {
        "RunSwebench.run_benchmark": 4,
    },
    "agent_forge/bench/application/campaign.py": {
        "RunBenchmarkCampaign.run_campaign": 3,
    },
    "agent_forge/bench/adapters/case_runtime.py": {
        "LocalCaseExecutor.run": 4,
    },
    "agent_forge/bench/domain/failure_taxonomy.py": {
        "classify_case_result": 5,
    },
    "agent_forge/evaluation/adapters/feedback_dataset_files.py": {
        "write_improvement_record": 4,
    },
    "agent_forge/observability/domain/usage.py": {
        "build_usage_report": 3,
    },
}

# 这些类是阅读和运行主链会直接遇到的应用服务。它们的公开方法名必须单独
# 表达业务动作，不能退化为脱离类名后没有信息量的通用动词。
CRITICAL_APPLICATION_SERVICES = {
    "agent_forge/runtime/application/run_preparation.py": {
        "RunPreparation",
    },
    "agent_forge/runtime/application/model_step_preparation.py": {
        "ModelStepPreparation",
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
    "agent_forge/bench/application/failure_analysis.py": {
        "BenchFailureAnalyzer",
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
    "attach",
    "check",
    "describe",
    "diagnose",
    "execute",
    "handle",
    "process",
    "start",
    "stop",
    "update",
}

# 这些记录包含多个同类型业务字段。位置参数会让调用处只能靠字段顺序猜含义。
KEYWORD_ONLY_RECORDS = {
    "agent_forge/bench/domain/failure_taxonomy.py": {"FailureDiagnosis"},
    "agent_forge/bench/application/campaign.py": {"BenchmarkCampaignResult"},
    "agent_forge/context/application/compaction.py": {
        "PromptWindowRequest",
        "PromptWindowResult",
        "PromptBudget",
    },
    "agent_forge/runtime/adapters/model_gateway.py": {"RetryPolicy"},
    "agent_forge/runtime/adapters/tool_call_normalizer.py": {
        "ToolCallNormalizationResult",
    },
    "agent_forge/runtime/application/operation_tracker.py": {
        "ExistingOperationResolution",
        "OperationIntent",
    },
    "agent_forge/runtime/application/run_lifecycle.py": {
        "HumanInputResolution",
        "RunLifecycle",
        "StopRequest",
    },
    "agent_forge/runtime/application/run_control.py": {"RunControlOutcome"},
    "agent_forge/runtime/application/model_step_preparation.py": {"PreparedModelStep"},
    "agent_forge/runtime/application/clarification.py": {"ClarificationDecision"},
    "agent_forge/runtime/application/step_control.py": {
        "ExecutionBudget",
        "FailureSignal",
        "StepController",
    },
    "agent_forge/runtime/domain/governance.py": {
        "HookContext",
        "HookDecision",
        "HookResult",
        "ModelHookContext",
    },
    "agent_forge/runtime/domain/operation.py": {
        "OperationPlan",
        "OperationRecord",
        "OperationTarget",
        "OperationTransition",
    },
    "agent_forge/runtime/domain/run_control.py": {"RunControlSignal"},
    "agent_forge/runtime/adapters/execution_environment.py": {
        "EnvironmentProbe",
        "ExecutionEnvironmentConfig",
    },
    "agent_forge/runtime/ports/context.py": {"TurnSystemContextRequest"},
    "agent_forge/runtime/wiring.py": {
        "AgentLoopBuildRequest",
        "HumanInputResponseCommand",
        "RuntimeDependencyOverrides",
        "ToolRegistryBuildRequest",
    },
    "agent_forge/safety/guardrails.py": {"GuardrailResult"},
    "agent_forge/skills/models.py": {"ActivatedSkill", "SkillSpec"},
    "agent_forge/tools/tool_router.py": {"ToolRoute", "ToolRoutingRequest"},
}

# 这些裸变量名只说明英文形容词，不说明“哪个业务对象、处于什么角色”。
AMBIGUOUS_CONTROL_VARIABLE_NAMES = {"denied", "effective", "lowered"}
APPLICATION_DIRECTORIES = (
    "agent_forge/runtime/application",
    "agent_forge/multi_agent/application",
    "agent_forge/bench/application",
    "agent_forge/evaluation/application",
)

# 同一个短名字在五行 serializer 中可能清楚，在状态 owner 中却会遮住业务角色。
# 因此这里按模块保护已经确认过的高风险名字，不把正常 Python 语法或通用局部变量
# 做成全仓禁令。
MODULE_SCOPED_VAGUE_LOCAL_NAMES = {
    "agent_forge/bench/application/campaign.py": {
        "item",
        "public_dir",
        "record",
    },
    "agent_forge/evaluation/application/scorecard.py": {
        "item",
        "raw_cases",
        "root",
    },
    "agent_forge/multi_agent/application/fanout.py": {
        "apply_detail",
        "item",
        "result",
        "results",
        "run_status",
        "run_summary",
        "status",
    },
    "agent_forge/runtime/application/operation_tracker.py": {
        "existing",
        "record",
        "stale",
        "update",
    },
    "agent_forge/runtime/application/operator_control.py": {"request"},
    "agent_forge/runtime/application/run_control.py": {
        "messages",
        "prior",
        "signal",
        "status",
        "steers",
        "terminal",
    },
    "agent_forge/runtime/application/tool_feedback.py": {
        "normalized_observation",
        "signal",
        "status",
        "unavailable",
    },
    "agent_forge/runtime/domain/task.py": {"status", "summary"},
    "agent_forge/runtime/adapters/execution_environment.py": {
        "output",
        "path",
        "result",
        "suffix",
        "target",
        "value",
    },
    "agent_forge/runtime/adapters/git_workspace.py": {
        "additions",
        "lines",
        "normalized",
        "path",
        "result",
        "tracked",
    },
    "agent_forge/runtime/adapters/hook_manager.py": {
        "current",
        "decision",
        "invoke",
        "key",
        "mapping",
        "ok",
        "reason",
        "stage",
        "value",
    },
    "agent_forge/runtime/adapters/openai_compatible.py": {
        "catalog",
        "choices",
        "content",
        "data",
        "instruction",
        "item",
        "message",
        "normalized",
        "payload",
        "raw",
        "request",
        "response",
    },
    "agent_forge/context/application/compaction.py": {
        "after",
        "before",
        "best",
        "candidate",
        "cut",
        "digest",
        "omitted",
        "recent",
        "result",
        "segments",
        "target",
    },
    "agent_forge/memory/application/service.py": {
        "current",
        "item",
        "merged",
        "old",
        "previous",
        "record",
        "score",
        "scored",
    },
    "agent_forge/runtime/adapters/model_gateway.py": {
        "attempt",
        "client",
        "code",
        "error",
        "next_messages",
        "response",
        "usage",
    },
    "agent_forge/runtime/adapters/tool_call_normalizer.py": {
        "arguments",
        "calls",
        "candidates",
        "data",
        "function",
        "names",
        "raw",
        "repairs",
        "rows",
        "source",
    },
    "agent_forge/runtime/wiring.py": {
        "config",
        "environment",
        "hooks",
        "request",
        "selected",
    },
}

# 这些方法构成核心运行主干。结构化事件字段只能出现在具名证据记录器中。
TRACE_FREE_ORCHESTRATION_METHODS = {
    "agent_forge/runtime/application/agent_loop.py": {
        "_call_model",
        "_handle_model_failure",
        "_run_model_step",
        "run",
    },
    "agent_forge/runtime/application/final_answer.py": {"build_stop_request"},
    "agent_forge/runtime/application/operation_tracker.py": {
        "record_executing",
        "record_execution_result",
        "resolve_existing_operation",
    },
    "agent_forge/runtime/application/run_control.py": {"consume_pending_signals"},
    "agent_forge/runtime/application/run_lifecycle.py": {
        "finalize_run",
        "request_human_input",
    },
    "agent_forge/runtime/application/run_preparation.py": {
        "_apply_input_policy",
        "_ensure_turn_context_snapshot",
        "_load_resume_checkpoint",
        "_resolve_clarification",
        "_select_active_skills",
        "create_session",
        "prepare_run",
    },
    "agent_forge/runtime/application/tool_authorization.py": {
        "_resolve_approval",
        "authorize",
    },
    "agent_forge/runtime/application/tool_execution.py": {
        "_execute_call",
        "_run_tool",
        "execute_calls",
    },
    "agent_forge/runtime/application/model_step_preparation.py": {"prepare_model_step"},
}

# 这些模块会产生 Runtime/Fanout 事实。原始 EventSink.add 只能留在具名
# ``_record_*`` 叶子中，避免业务主链重新被 JSON payload 拼装淹没。
EVIDENCE_RECORDER_MODULES = {
    "agent_forge/runtime/application/agent_loop.py",
    "agent_forge/runtime/application/final_answer.py",
    "agent_forge/runtime/application/operation_tracker.py",
    "agent_forge/runtime/application/run_control.py",
    "agent_forge/runtime/application/run_lifecycle.py",
    "agent_forge/runtime/application/run_preparation.py",
    "agent_forge/runtime/application/tool_authorization.py",
    "agent_forge/runtime/application/tool_execution.py",
    "agent_forge/runtime/application/tool_feedback.py",
    "agent_forge/runtime/application/model_step_preparation.py",
    "agent_forge/multi_agent/application/fanout.py",
}

# 这些消息/结果对象在核心流程中频繁出现，位置参数会迫使读者记字段顺序。
# 只约束 Runtime 应用层和 Hook 边界，不把测试 fixture 与简单 provider adapter
# 一并纳入，避免为形式统一制造无关改动。
EXPLICIT_CORE_RECORD_CALLS = {"Message", "Observation", "ToolCallOutcome"}
EXPLICIT_CORE_RECORD_DIRECTORIES = ("agent_forge/runtime/application",)
EXPLICIT_CORE_RECORD_FILES = ("agent_forge/runtime/adapters/hook_manager.py",)
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

# 这五个文件是 Multi-Agent 核心阅读主线的唯一深读 Owner。约束大区名称而不锁行号、
# 私有 helper 或小实现，保证 Collapse All 后先看到稳定架构骨架，也避免测试反过来
# 阻碍正常重构。
MULTI_AGENT_FOLD_MAP = {
    "agent_forge/multi_agent/application/fanout.py": {
        "Public 主链",
        "Worker 调度",
        "Candidate 集成",
        "有界恢复与 checkpoint",
        "Trace 证据",
    },
    "agent_forge/multi_agent/application/live_handoff.py": {
        "Attempt 生命周期",
        "发布与投递",
        "最终集成门禁",
        "Generation、唤醒与只读查询",
    },
    "agent_forge/multi_agent/application/planning.py": {
        "Planner 主链",
        "Artifact 与 resume",
    },
    "agent_forge/multi_agent/adapters/local_worker.py": {
        "Worker 生命周期",
        "Finalizer",
        "恢复验证",
    },
    "agent_forge/multi_agent/domain/live.py": {
        "唯一执行计划",
        "结果与持久化 contract",
        "输入规范化与组合图校验",
    },
}

# 架构讲解会从产品入口一路下钻到 Application、Domain 与 Adapter。这里保护的是
# 每项能力真正的阅读 Owner，不把纯 DTO、re-export、路径 helper 或页面渲染机械纳入。
# 这样既能保证 Collapse All 后先看到架构骨架，也不会让注释本身成为新的搜索噪音。
ARCHITECTURE_NAVIGATION_CLOSURE = {
    "agent_forge/runtime/domain/thread.py": {
        "Conversation journal",
        "Thread / Turn / Run",
        "Turn context state",
    },
    "agent_forge/runtime/adapters/thread_json.py": {
        "ownership",
        "Conversation journal",
        "immutable Turn snapshot",
        "崩溃修复",
    },
    "agent_forge/runtime/application/session.py": {
        "Conversation 事务页",
        "Provider 顺序投影",
        "Run session state",
    },
    "agent_forge/runtime/wiring.py": {
        "装配请求",
        "Tool / Model Adapter",
        "Runtime dependencies",
        "Canonical AgentLoop",
    },
    "agent_forge/runtime/adapters/operation_ledger_json.py": {
        "身份与 Fingerprint",
        "公开状态迁移",
        "锁内提交",
    },
    "agent_forge/runtime/adapters/execution_environment.py": {
        "生命周期与模式选择",
        "路径、命令和敏感信息",
        "Cleanup 与 evidence",
        "命令执行",
    },
    "agent_forge/context/application/compaction.py": {
        "增量基线",
        "安全切分",
        "Rolling 候选搜索",
        "保守回退",
    },
    "agent_forge/context/application/context_builder.py": {
        "一次性发现",
        "独立预算",
        "动态候选",
    },
    "agent_forge/tools/tool_router.py": {
        "候选目录",
        "通用任务意图",
        "专项策略",
        "输出投影",
    },
    "agent_forge/tools/registry.py": {
        "Registry 与 schemas",
        "唯一执行出口",
        "物理参数校验",
    },
    "agent_forge/memory/application/service.py": {
        "CREATE",
        "UPDATE/NOOP",
        "同 key 冲突",
        "有界 Catalog",
    },
    "agent_forge/bench/application/swebench.py": {
        "Case 执行",
        "Official evaluation",
        "诊断与发布",
    },
    "agent_forge/evaluation/application/scorecard.py": {
        "主要入口",
    },
    "agent_forge/observability/domain/usage.py": {
        "聚合容器",
        "事件投影",
        "派生指标",
    },
    "agent_forge/safety/sandbox.py": {
        "Canonical 路径解析",
        "敏感路径分类",
        "强制 workspace",
    },
    "agent_forge/skills/registry.py": {
        "核心主链",
        "启动装配与目录管理",
    },
    "apps/repository_run.py": {
        "公开策略入口",
        "Multi-Agent Run",
        "Runtime 装配",
        "Validated Plan 执行",
    },
    "apps/operator_console/application/thread_library.py": {
        "创建与查询",
        "导航 metadata",
        "标题规范化",
    },
    "apps/workbench/adapters/evidence_files.py": {
        "Canonical Lab / Mini-50",
        "Fallback 与历史来源",
        "公开 artifact 查询",
    },
    "apps/workbench/application/context_inspection.py": {
        "逐 Model Step 聚合",
        "ToolCall / Observation 配对",
        "阶段与关键点分类",
    },
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

    def test_skill_registry_puts_the_three_step_control_chain_first(self) -> None:
        """Skill 辅助装配不能挡在 select/discover/activate 主链前面。"""

        registry_path = PROJECT_ROOT / "agent_forge/skills/registry.py"
        registry_tree = ast.parse(registry_path.read_text(encoding="utf-8"))
        registry_class = _find_owner(registry_tree, "SkillRegistry")
        self.assertIsInstance(registry_class, ast.ClassDef)
        assert isinstance(registry_class, ast.ClassDef)
        method_names = [
            node.name
            for node in registry_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(
            method_names[:4],
            ["__init__", "select_for_task", "discover_for_task", "activate"],
        )
        self.assertLessEqual(
            len(registry_path.read_text(encoding="utf-8").splitlines()),
            300,
            "Skill 主链文件过长时，应把数据映射或包 I/O 移出 registry.py",
        )

        support_path = PROJECT_ROOT / "agent_forge/skills/_package_support.py"
        support_tree = ast.parse(support_path.read_text(encoding="utf-8"))
        self.assertIn("不参与运行时选择决策", ast.get_docstring(support_tree) or "")

    def test_core_workflows_partition_long_flows_for_collapse_all(self) -> None:
        """核心长入口必须说明阶段，并保持 region 成对。"""

        for relative_path, owners in CORE_WORKFLOW_ENTRYPOINTS.items():
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            source_lines = source.splitlines()
            tree = ast.parse(source)
            for owner_name, minimum_regions in owners.items():
                owner = _find_owner(tree, owner_name)
                self.assertIsInstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
                assert isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
                method_source = _function_source_with_trailing_regions(
                    source_lines,
                    owner,
                )
                region_count = method_source.count("# region ")
                endregion_count = method_source.count("# endregion ")
                with self.subTest(path=relative_path, owner=owner_name):
                    self.assertTrue(ast.get_docstring(owner))
                    self.assertGreaterEqual(region_count, minimum_regions)
                    self.assertEqual(
                        region_count,
                        endregion_count,
                        f"unpaired regions in {relative_path}:{owner_name}",
                    )

    def test_long_core_workflow_regions_explain_internal_calls(self) -> None:
        """长 region 不能只有标题；内部还要解释关键调用和状态语义。"""

        for relative_path, owners in CORE_WORKFLOW_ENTRYPOINTS.items():
            source_lines = (
                (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").splitlines()
            )
            tree = ast.parse("\n".join(source_lines))
            for owner_name in owners:
                owner = _find_owner(tree, owner_name)
                self.assertIsInstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
                assert isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
                unexplained_regions = _long_regions_without_navigation_comment(
                    source_lines,
                    owner,
                )
                with self.subTest(path=relative_path, owner=owner_name):
                    self.assertEqual(
                        unexplained_regions,
                        [],
                        "long core regions need a call-site navigation comment",
                    )

    def test_multi_agent_core_owners_keep_a_foldable_architecture_map(self) -> None:
        """Multi-Agent 深读文件折叠后必须保留少量、可搜索的大阶段。"""

        for relative_path, expected_sections in MULTI_AGENT_FOLD_MAP.items():
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source)
            region_titles = [
                line.strip().removeprefix("# region ")
                for line in source.splitlines()
                if line.strip().startswith("# region ")
                and len(line) - len(line.lstrip()) <= 4
            ]
            with self.subTest(path=relative_path):
                self.assertTrue(ast.get_docstring(tree))
                self.assertEqual(
                    source.count("# region "),
                    source.count("# endregion "),
                    "all folding regions must remain paired",
                )
                for section in expected_sections:
                    self.assertTrue(
                        any(section in title for title in region_titles),
                        f"missing foldable architecture section: {section}",
                    )

    def test_architecture_navigation_closure_keeps_roles_and_fold_map(self) -> None:
        """六层核心 Owner 必须先说明角色，并保留可折叠的阅读地图。"""

        for relative_path, expected_sections in ARCHITECTURE_NAVIGATION_CLOSURE.items():
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source)
            with self.subTest(path=relative_path):
                self.assertIn(
                    "系统角色",
                    ast.get_docstring(tree) or "",
                    "architecture owner needs role/input/output boundary",
                )
                self.assertEqual(
                    source.count("# region "),
                    source.count("# endregion "),
                    "all folding regions must remain paired",
                )
                for section in expected_sections:
                    self.assertIn(
                        section,
                        source,
                        f"missing architecture navigation section: {section}",
                    )

    def test_multi_agent_control_flow_reads_like_pseudocode(self) -> None:
        """Multi-Agent 及其协调主链的分支前必须先说明业务目的。"""

        missing_navigation_comments: list[str] = []
        multi_agent_root = PROJECT_ROOT / "agent_forge/multi_agent"
        coordination_runtime_paths = [
            PROJECT_ROOT / "agent_forge/runtime/domain/run_control.py",
            PROJECT_ROOT / "agent_forge/runtime/application/run_control.py",
            PROJECT_ROOT / "agent_forge/runtime/application/agent_loop.py",
        ]
        navigation_paths = [
            *sorted(multi_agent_root.rglob("*.py")),
            *coordination_runtime_paths,
        ]

        # 一条粗粒度规则覆盖能力包及真实 AgentLoop 接缝，避免维护细碎方法白名单。
        for path in navigation_paths:
            source_lines = path.read_text(encoding="utf-8").splitlines()
            tree = ast.parse("\n".join(source_lines), filename=str(path))

            # 只约束会改变执行路径的语句；简单赋值、返回和序列化不机械加注释。
            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try),
                ):
                    continue
                previous_line_index = node.lineno - 2
                while (
                    previous_line_index >= 0
                    and not source_lines[previous_line_index].strip()
                ):
                    previous_line_index -= 1
                if previous_line_index < 0 or not source_lines[
                    previous_line_index
                ].lstrip().startswith("#"):
                    relative_path = path.relative_to(PROJECT_ROOT)
                    missing_navigation_comments.append(
                        f"{relative_path}:{node.lineno}:{type(node).__name__}"
                    )

        self.assertEqual(
            missing_navigation_comments,
            [],
            "Multi-Agent control flow needs a preceding navigation comment",
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
        """关键调用点应直接说明动作，不依赖所属类补充方法语义。"""

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

    def test_semantic_records_require_keyword_construction(self) -> None:
        """同类型字段较多的核心记录不能退回依赖参数顺序的构造方式。"""

        for relative_path, class_names in KEYWORD_ONLY_RECORDS.items():
            tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
            for class_name in class_names:
                owner = _find_named(tree.body, class_name)
                self.assertIsInstance(owner, ast.ClassDef)
                assert isinstance(owner, ast.ClassDef)
                with self.subTest(path=relative_path, owner=class_name):
                    self.assertTrue(_dataclass_uses_keyword_only_fields(owner))

    def test_core_runtime_constructs_messages_and_outcomes_with_keywords(self) -> None:
        """核心消息与结果必须在调用处直接写出每个字段的业务含义。"""

        checked_paths = [
            path
            for directory in EXPLICIT_CORE_RECORD_DIRECTORIES
            for path in (PROJECT_ROOT / directory).rglob("*.py")
        ]
        checked_paths.extend(PROJECT_ROOT / path for path in EXPLICIT_CORE_RECORD_FILES)

        for path in checked_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            positional_record_calls = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and _called_symbol_name(node) in EXPLICIT_CORE_RECORD_CALLS
                and node.args
            ]
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertEqual(positional_record_calls, [])

    def test_orchestration_methods_delegate_trace_payload_construction(self) -> None:
        """主流程只出现具名证据动作，不展开 EventSink.add 的字段拼装。"""

        for relative_path, method_names in TRACE_FREE_ORCHESTRATION_METHODS.items():
            tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
            for method_name in method_names:
                method = next(
                    (
                        node
                        for node in ast.walk(tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == method_name
                    ),
                    None,
                )
                self.assertIsNotNone(
                    method, f"missing method: {relative_path}:{method_name}"
                )
                assert isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                direct_trace_adds = [
                    node.lineno
                    for node in ast.walk(method)
                    if _is_direct_trace_add(node)
                ]
                with self.subTest(path=relative_path, method=method_name):
                    self.assertEqual(direct_trace_adds, [])

    def test_raw_evidence_payloads_only_live_in_named_recorders(self) -> None:
        """Application 主链只说记录什么；字段拼装集中在可折叠叶子。"""

        for relative_path in EVIDENCE_RECORDER_MODULES:
            tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
            offenders = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_record_")
                and any(_is_direct_trace_add(child) for child in ast.walk(node))
            }
            with self.subTest(path=relative_path):
                self.assertEqual(offenders, set())

    def test_core_runtime_avoids_nested_ternary_control_flow(self) -> None:
        """多分支业务状态使用显式 if/elif，不能依赖三元表达式结合顺序。"""

        for relative_path in RUNTIME_CORE:
            tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
            nested_ternaries = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.IfExp)
                and (
                    isinstance(node.body, ast.IfExp)
                    or isinstance(node.orelse, ast.IfExp)
                )
            ]
            with self.subTest(path=relative_path):
                self.assertEqual(nested_ternaries, [])

    def test_typed_runtime_config_is_accessed_without_getattr(self) -> None:
        """RuntimeConfig 已声明字段，Core 不得再用动态兼容读取掩盖契约。"""

        for path in (PROJECT_ROOT / "agent_forge").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            dynamic_config_reads = [
                node.lineno for node in ast.walk(tree) if _is_typed_config_getattr(node)
            ]
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertEqual(dynamic_config_reads, [])

    def test_application_control_variables_name_the_business_role(self) -> None:
        """关键判断结果必须说明对象角色，不能只叫 effective/denied/lowered。"""

        for directory in APPLICATION_DIRECTORIES:
            for path in (PROJECT_ROOT / directory).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                ambiguous_assignments = {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Store)
                    and node.id in AMBIGUOUS_CONTROL_VARIABLE_NAMES
                }
                with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                    self.assertEqual(ambiguous_assignments, set())

    def test_state_owner_modules_avoid_context_free_local_names(self) -> None:
        """已审计的 owner 不应退回只能靠上下文猜测的裸局部变量名。"""

        for relative_path, forbidden_names in MODULE_SCOPED_VAGUE_LOCAL_NAMES.items():
            tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
            vague_local_assignments = (
                _stored_names_inside_functions(tree) & forbidden_names
            )
            with self.subTest(path=relative_path):
                self.assertEqual(vague_local_assignments, set())

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


def _function_source_with_trailing_regions(
    source_lines: list[str],
    owner: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    """包含 return 后、仍位于函数缩进内的 ``# endregion`` 注释。

    Python AST 的 ``end_lineno`` 不包含尾部注释；PyCharm folding 却会处理这些注释。
    这里只向后读取更深缩进的行，避免误吞类级或模块级 region。
    """

    end_index = owner.end_lineno
    while end_index < len(source_lines):
        line = source_lines[end_index]
        if not line.strip():
            end_index += 1
            continue
        indentation = len(line) - len(line.lstrip())
        if indentation <= owner.col_offset:
            break
        end_index += 1
    return "\n".join(source_lines[owner.lineno - 1 : end_index])


def _long_regions_without_navigation_comment(
    source_lines: list[str],
    owner: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[int]:
    """返回只有 region 标题、却没有内部导航注释的长逻辑段起始行。"""

    region_stack: list[tuple[int, list[str]]] = []
    unexplained_region_lines: list[int] = []
    for line_number in range(owner.lineno, owner.end_lineno + 1):
        line = source_lines[line_number - 1]
        stripped = line.strip()
        if stripped.startswith("# region "):
            region_stack.append((line_number, []))
            continue
        if stripped.startswith("# endregion ") and region_stack:
            region_start, region_body = region_stack.pop()
            code_line_count = sum(
                1
                for body_line in region_body
                if body_line.strip() and not body_line.strip().startswith("#")
            )
            has_navigation_comment = any(
                body_line.strip().startswith("#")
                and not body_line.strip().startswith(("# region ", "# endregion "))
                for body_line in region_body
            )
            if code_line_count >= 10 and not has_navigation_comment:
                unexplained_region_lines.append(region_start)
            continue
        if region_stack:
            region_stack[-1][1].append(line)
    return unexplained_region_lines


def _dataclass_uses_keyword_only_fields(owner: ast.ClassDef) -> bool:
    for decorator in owner.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not isinstance(decorator.func, ast.Name) or decorator.func.id != "dataclass":
            continue
        return any(
            keyword.arg == "kw_only"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        )
    return False


def _is_direct_trace_add(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "add":
        return False
    target = node.func.value
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and target.attr in {"trace", "events"}
    )


def _called_symbol_name(node: ast.Call) -> str:
    """返回 ``Message(...)`` 或 ``module.Message(...)`` 的末级符号名。"""

    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _is_typed_config_getattr(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Name) or node.func.id != "getattr":
        return False
    if not node.args:
        return False
    config_expression = node.args[0]
    if isinstance(config_expression, ast.Name):
        return config_expression.id == "config"
    return (
        isinstance(config_expression, ast.Attribute)
        and isinstance(config_expression.value, ast.Name)
        and config_expression.value.id == "self"
        and config_expression.attr in {"config", "base_config"}
    )


def _stored_names_inside_functions(tree: ast.Module) -> set[str]:
    """返回函数体内写入的局部名，排除 dataclass/TypedDict 字段声明。"""

    function_ranges = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
        and any(start <= node.lineno <= end for start, end in function_ranges)
    }


if __name__ == "__main__":
    unittest.main()
