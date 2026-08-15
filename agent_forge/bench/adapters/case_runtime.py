"""高级 SWE-bench 单题 Runtime 适配器。

生产调用链由 ``bench.wiring.build_swebench_runner`` 经 ``CaseExecutorPort`` 注入本类，
再由 ``RunSwebench.run_benchmark`` 对每个 case 调用 ``run``。它不是测试辅助代码，
因此位于
12-file Runtime Core 之外。

Single-Agent 分支仍负责 benchmark 特有的 workspace/artifact 映射。只有真实 case 已证明
candidate diff、trace、memory namespace、清理与 official layout 兼容，才把该分支迁到
``Harness`` 后方。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from agent_forge.bench.adapters.git_workspace import (
    SwebenchWorkspaceManager,
    collect_candidate_diff,
    ensure_clean_git,
)
from agent_forge.bench.adapters.local_validation import read_local_validation
from agent_forge.bench.domain.config import SwebenchRunRequest, safe_id
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult
from agent_forge.bench.ports import CaseExecutorPort
from agent_forge.models.gateway import ModelGateway
from agent_forge.multi_agent.profiles import get_profile
from agent_forge.multi_agent.wiring import (
    SequentialCoordinatorBuildRequest,
    build_multi_agent_coordinator,
)
from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.observability.api import write_usage_artifacts
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.llm_config import LLMConfigRequest, resolve_llm_config
from agent_forge.runtime.wiring import (
    ToolRegistryBuildRequest,
    build_llm,
    build_registry,
)


class LocalCaseExecutor(CaseExecutorPort):
    """把一个类型化 benchmark case 映射到 Runtime 执行与 Evidence 文件。"""

    def __init__(self, workspace_manager: SwebenchWorkspaceManager) -> None:
        self._workspace_manager = workspace_manager

    # 主要入口：准备单题隔离环境，运行真实 Runtime，收集 candidate diff 与 trace。
    def run(
        self,
        case: BenchCase,
        *,
        case_dir: Path,
        agent_mode: str,
        request: SwebenchRunRequest,
    ) -> BenchCaseResult:
        """由 ``RunSwebench`` 调用；返回 candidate diff/local status，不判定 official outcome。"""

        # region 1. Case 沙箱与输出契约：固定 workspace、trace、diff 和默认失败状态
        # CaseExecutor 默认 BLOCKED，并预先固定所有 artifact 路径；任何准备或 Provider
        # 异常仍会留下可分类的 trace/diff，而不会被上层误判成 no-patch。
        workspace = self._workspace_manager.prepare(
            case,
            agent_mode if agent_mode in {"single", "multi"} else "",
        )
        active_workspace = workspace
        case_dir.mkdir(parents=True, exist_ok=True)
        trace_path = case_dir / "trace.json"
        candidate_diff_path = case_dir / "candidate_changes.diff"
        final_answer = ""
        usage_report_path: Path | None = None
        status = "blocked"
        error = ""
        environment: ExecutionEnvironment | None = None
        # endregion 1. Case 沙箱与输出契约结束

        try:
            # region 2. Runtime 装配：渲染任务，并为本 Case 创建隔离环境与端口
            # 每题使用独立 ExecutionEnvironment、Registry、Model 和状态根目录；
            # RuntimeConfig 完整复制实验参数，使 Case 结果可以追溯到同一配置身份。
            ensure_clean_git(workspace)
            task = render_case_task(case)
            trace = TraceRecorder(str(trace_path))
            environment = self._prepare_environment(
                workspace,
                case,
                agent_mode,
                request,
            )
            active_workspace = environment.active_workspace
            registry = build_registry(
                ToolRegistryBuildRequest(
                    workspace=str(active_workspace),
                    auto=True,
                    execution_environment=environment,
                    tool_execution_timeout_seconds=(
                        request.tool_execution_timeout_seconds
                    ),
                    memory_root=(
                        request.memory_root or str(case_dir / "disabled_memory")
                    ),
                    memory_namespace=(
                        request.memory_namespace or f"swebench:{case.instance_id}"
                    ),
                )
            )
            llm, model_capabilities = self._build_model(request)
            runtime_config = RuntimeConfig(
                workspace=str(active_workspace),
                max_steps=request.max_steps,
                trace_file=str(trace_path),
                max_context_chars=request.max_context_chars,
                max_prompt_tokens=request.max_prompt_tokens,
                reserved_output_tokens=request.reserved_output_tokens,
                max_tool_calls_per_turn=request.max_tool_calls_per_turn,
                timeout_seconds=request.timeout_seconds,
                tool_execution_timeout_seconds=(request.tool_execution_timeout_seconds),
                cost_budget_usd=request.cost_budget_usd,
                task_state_root=str(case_dir / "task_state"),
                tool_routing_mode=request.tool_routing_mode,
                skill_mode=request.skill_mode,
                skill_names=list(request.skill_names),
                skill_manifest_files=list(request.skill_manifest_files),
                memory_root=(request.memory_root or str(case_dir / "disabled_memory")),
                memory_namespace=(
                    request.memory_namespace or f"swebench:{case.instance_id}"
                ),
                memory_max_chars=request.memory_max_chars,
                model_capabilities=model_capabilities,
                execution_environment=environment,
            )
            # endregion 2. Runtime 装配结束

            # region 3. Agent 执行与候选采集：运行真实主链，随后冻结 trace/usage/diff
            # _execute_runtime 只返回 Agent 最终文本；运行后再从实际 workspace 收集 diff，
            # status 必须同时依据候选改动和停止文本，而不能仅相信模型自报完成。
            final_answer = self._execute_runtime(
                task,
                agent_mode,
                request,
                runtime_config,
                trace,
                registry,
                llm,
                case_dir,
            )
            trace.write()
            _, usage_report_path = write_usage_artifacts(trace_path)
            candidate_diff_text = collect_candidate_diff(active_workspace)
            candidate_diff_path.write_text(candidate_diff_text, encoding="utf-8")
            status = _run_status(candidate_diff_text, final_answer)
            # endregion 3. Agent 执行与候选采集结束
        except Exception as exc:
            # 异常也必须产出最小 trace/diff，使上层能区分环境失败与 Agent 失败。
            error = str(exc)
            candidate_diff_path.write_text("", encoding="utf-8")
            if not trace_path.exists():
                trace_path.write_text(
                    json.dumps({"error": error}, indent=2),
                    encoding="utf-8",
                )
        finally:
            error = self._finalize_environment(environment, case_dir, error)

        # region 4. 本地证据映射：只报告 candidate/local validation，不越权判 official
        local_validation = read_local_validation(trace_path)
        patch_chars = (
            len(candidate_diff_path.read_text(encoding="utf-8"))
            if candidate_diff_path.exists()
            else 0
        )
        return BenchCaseResult(
            instance_id=case.instance_id,
            repo=case.repo,
            workspace=active_workspace,
            trace_path=trace_path,
            usage_report_path=usage_report_path,
            candidate_diff_path=candidate_diff_path,
            status=status,
            final_answer=final_answer,
            patch_chars=patch_chars,
            error=error,
            evaluation_status=(
                "local_verified"
                if local_validation.status == "passed"
                else "not_evaluated"
            ),
            local_validation_status=local_validation.status,
            local_validation_evidence=local_validation.evidence,
        )
        # endregion 4. 本地证据映射结束

    @staticmethod
    def _prepare_environment(
        workspace: Path,
        case: BenchCase,
        agent_mode: str,
        request: SwebenchRunRequest,
    ) -> ExecutionEnvironment:
        environment = ExecutionEnvironment(
            ExecutionEnvironmentConfig(
                mode=request.execution_mode,
                workspace=str(workspace),
                run_id=(
                    f"{safe_id(case.instance_id)}-{agent_mode}-{uuid.uuid4().hex[:7]}"
                ),
                network_policy=request.network_policy,
                keep_worktree=request.keep_worktree,
                container_runtime=request.container_runtime,
                container_image=request.container_image,
                container_cpus=request.container_cpus,
                container_memory=request.container_memory,
                container_pids_limit=request.container_pids_limit,
                container_read_only=request.container_read_only,
            )
        )
        environment.prepare()
        return environment

    @staticmethod
    def _build_model(
        request: SwebenchRunRequest,
    ) -> tuple[ModelGateway, ModelCapabilities]:
        """同时返回模型 Adapter 与能力事实，避免 Runtime 再退回 32K 默认值。"""

        llm_config = resolve_llm_config(
            LLMConfigRequest(
                provider=request.provider,
                base_url=request.base_url,
                api_key=request.api_key,
                model=request.model,
                timeout=request.model_request_timeout_seconds,
                temperature=request.temperature,
                thinking_mode=request.thinking_mode,
                reasoning_effort=request.reasoning_effort,
            )
        )
        if not llm_config.is_configured():
            raise RuntimeError(
                f"{request.provider} model config is incomplete; "
                "set API key/base URL/model."
            )
        model_capabilities = getattr(llm_config, "capabilities", None)
        if not isinstance(model_capabilities, ModelCapabilities):
            # 正式解析器始终提供能力事实；兼容自定义 Adapter 与测试替身时，
            # 使用 Runtime 的明确默认值，而不是因缺少可选属性阻断整个 Case。
            model_capabilities = ModelCapabilities()
        return (
            build_llm(
                llm_config,
                max_attempts=request.model_request_max_attempts,
            ),
            model_capabilities,
        )

    @staticmethod
    def _execute_runtime(
        task: str,
        agent_mode: str,
        request: SwebenchRunRequest,
        runtime_config: RuntimeConfig,
        trace: TraceRecorder,
        registry: Any,
        llm: ModelGateway,
        case_dir: Path,
    ) -> str:
        if agent_mode == "multi":
            return (
                build_multi_agent_coordinator(
                    SequentialCoordinatorBuildRequest(
                        task=task,
                        profile=get_profile(request.profile),
                        runtime_config=runtime_config,
                        trace=trace,
                        registry=registry,
                        llm=llm,
                        run_dir=case_dir,
                        max_revision_rounds=request.max_revision_rounds,
                    )
                )
                .run()
                .final_answer
            )
        return build_agent_loop(runtime_config, trace, registry, llm).run(task)

    @staticmethod
    def _finalize_environment(
        environment: ExecutionEnvironment | None,
        case_dir: Path,
        error: str,
    ) -> str:
        if environment is None:
            return error
        try:
            environment.write_manifest(case_dir)
        except Exception as exc:
            detail = f"execution manifest failed: {exc}"
            error = f"{error}; {detail}" if error else detail
        try:
            environment.cleanup()
        except Exception as exc:
            detail = f"execution cleanup failed: {exc}"
            error = f"{error}; {detail}" if error else detail
        return error


def render_case_task(case: BenchCase) -> str:
    return (
        "Resolve this SWE-bench coding issue.\n\n"
        f"Instance: {case.instance_id}\n"
        f"Repository: {case.repo}\n"
        f"Base commit: {case.base_commit}\n\n"
        "Issue:\n"
        f"{case.problem_statement}\n\n"
        "Operating rules:\n"
        "- Inspect the repository before editing.\n"
        "- Make the smallest source-code patch that addresses the issue.\n"
        "- Do not edit tests unless the issue explicitly requires test infrastructure changes.\n"
        "- Use read_file/grep_search for source inspection; do not use run_command for reading files.\n"
        "- Prefer replace_text once the likely target function is identified; do not keep gathering broad evidence.\n"
        "- For focused validation, call python_validation with check_type=pytest and the smallest relevant "
        "existing test path or pytest node id. Use check_type=unittest only for unittest suites.\n"
        "- If the bounded validator collects no tests or the repository needs project-specific pytest flags, "
        "use the allowlisted run_command fallback; never use it to read files.\n"
        "- Do not use python -c, shell pipes, redirection, or /tmp files.\n"
        "- If validation is blocked, keep the patch and clearly explain the unverified point instead of spending more steps.\n"
        "- Finish with a concise summary grounded in files changed and commands run.\n"
    )


def _run_status(candidate_diff_text: str, final_answer: str) -> str:
    if candidate_diff_text.strip():
        return "patch_generated"
    if final_answer.startswith("blocked:"):
        return "blocked"
    return "no_patch"
