"""高级 SWE-bench 单题 Runtime 适配器。

生产调用链由 ``bench.wiring.build_swebench_runner`` 经 ``CaseExecutorPort`` 注入本类，
再由 ``RunSwebench.execute`` 对每个 case 调用 ``run``。它不是测试辅助代码，因此位于
12-file Runtime Core 之外。

Single-Agent 分支仍负责 benchmark 特有的 workspace/artifact 映射。只有真实 case 已证明
patch、trace、memory namespace、清理与 official layout 兼容，才把该分支迁到 ``Harness`` 后方。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from agent_forge.bench.adapters.git_workspace import (
    SwebenchWorkspaceManager,
    collect_patch,
    ensure_clean_git,
)
from agent_forge.bench.adapters.local_validation import read_local_validation
from agent_forge.bench.domain.config import SwebenchRunRequest, safe_id
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult
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
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.llm_config import LLMConfigRequest, resolve_llm_config
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.wiring import (
    ToolRegistryBuildRequest,
    build_llm,
    build_registry,
)


class LocalCaseExecutor:
    """把一个类型化 benchmark case 映射到 Runtime 执行与 Evidence 文件。"""

    def __init__(self, workspace_manager: SwebenchWorkspaceManager) -> None:
        self._workspace_manager = workspace_manager

    # 主要入口：准备单题隔离环境，运行真实 Runtime，收集 patch 与 trace。
    def run(
        self,
        case: BenchCase,
        *,
        case_dir: Path,
        agent_mode: str,
        request: SwebenchRunRequest,
    ) -> BenchCaseResult:
        """由 ``RunSwebench`` 调用；返回 patch/local status，不判定 official outcome。"""

        workspace = self._workspace_manager.prepare(
            case,
            agent_mode if agent_mode in {"single", "multi"} else "",
        )
        active_workspace = workspace
        case_dir.mkdir(parents=True, exist_ok=True)
        trace_path = case_dir / "trace.json"
        patch_path = case_dir / "patch.diff"
        final_answer = ""
        usage_report_path: Path | None = None
        status = "blocked"
        error = ""
        environment: ExecutionEnvironment | None = None
        try:
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
                )
            )
            llm = self._build_model(request)
            runtime_config = RuntimeConfig(
                workspace=str(active_workspace),
                max_steps=request.max_steps,
                trace_file=str(trace_path),
                max_context_chars=request.max_context_chars,
                max_prompt_tokens=request.max_prompt_tokens,
                reserved_output_tokens=request.reserved_output_tokens,
                max_tool_calls_per_turn=request.max_tool_calls_per_turn,
                timeout_seconds=request.timeout_seconds,
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
                memory_recall_limit=request.memory_recall_limit,
                execution_environment=environment,
            )
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
            patch = collect_patch(active_workspace)
            patch_path.write_text(patch, encoding="utf-8")
            status = _run_status(patch, final_answer)
        except Exception as exc:
            error = str(exc)
            patch_path.write_text("", encoding="utf-8")
            if not trace_path.exists():
                trace_path.write_text(
                    json.dumps({"error": error}, indent=2),
                    encoding="utf-8",
                )
        finally:
            error = self._finalize_environment(environment, case_dir, error)

        local_validation = read_local_validation(trace_path)
        patch_chars = (
            len(patch_path.read_text(encoding="utf-8")) if patch_path.exists() else 0
        )
        return BenchCaseResult(
            instance_id=case.instance_id,
            repo=case.repo,
            workspace=active_workspace,
            trace_path=trace_path,
            usage_report_path=usage_report_path,
            patch_path=patch_path,
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
    def _build_model(request: SwebenchRunRequest) -> ModelGateway:
        llm_config = resolve_llm_config(
            LLMConfigRequest(
                provider=request.provider,
                base_url=request.base_url,
                api_key=request.api_key,
                model=request.model,
                timeout=60,
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
        return build_llm(llm_config)

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


class DirectModelBaseline:
    def predict(
        self,
        case: BenchCase,
        request: SwebenchRunRequest,
    ) -> dict[str, Any]:
        llm_config = resolve_llm_config(
            LLMConfigRequest(
                provider=request.provider,
                base_url=request.base_url,
                api_key=request.api_key,
                model=request.model,
                timeout=60,
                temperature=request.temperature,
                thinking_mode=request.thinking_mode,
                reasoning_effort=request.reasoning_effort,
            )
        )
        model_name = f"direct-{request.provider}-{request.model or 'default'}"
        if not llm_config.is_configured():
            return {
                "instance_id": case.instance_id,
                "model_name_or_path": model_name,
                "model_patch": "",
                "error": f"{request.provider} model config is incomplete",
            }
        llm = build_llm(llm_config)
        response = llm.chat(
            [
                Message(
                    "system",
                    "You are a coding model baseline. Return only a unified diff patch. Do not explain.",
                ),
                Message(
                    "user",
                    f"Repository: {case.repo}\nBase commit: {case.base_commit}\n"
                    f"Issue:\n{case.problem_statement}",
                ),
            ],
            [],
        )
        usage: dict[str, Any] = {}
        if getattr(llm, "last_usage", None) is not None:
            usage = llm.last_usage.to_dict()
        model_patch = extract_diff(response.content or "")
        return {
            "instance_id": case.instance_id,
            "model_name_or_path": model_name,
            "model_patch": model_patch,
            "error": response.error or "",
            "failure_class": (
                "baseline_provider_error"
                if response.error
                else ""
                if model_patch
                else "no_patch_generated"
            ),
            "estimated_cost_usd": float(usage.get("estimated_cost_usd") or 0.0),
            "llm_calls": 1,
            "total_tokens": int(usage.get("total_tokens") or 0),
            "llm_latency_ms": int(usage.get("latency_ms") or 0),
            "tool_calls": 0,
            "failed_tool_calls": 0,
        }


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
        "- Prefer apply_patch once the likely target function is identified; do not keep gathering broad evidence.\n"
        "- For focused validation, call diagnostics with kind=pytest and the smallest relevant "
        "existing test path or pytest node id. Use kind=unittest only for unittest suites.\n"
        "- Do not use python -c, shell pipes, redirection, or /tmp files.\n"
        "- If validation is blocked, keep the patch and clearly explain the unverified point instead of spending more steps.\n"
        "- Finish with a concise summary grounded in files changed and commands run.\n"
    )


def extract_diff(text: str) -> str:
    stripped = text.strip()
    if "```" not in stripped:
        return stripped if looks_like_diff(stripped) else ""
    for chunk in stripped.split("```"):
        candidate = chunk.strip()
        if candidate.startswith("diff"):
            candidate = candidate[4:].strip()
        if looks_like_diff(candidate):
            return candidate
    return ""


def looks_like_diff(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("diff --git ") or (
        stripped.startswith("--- ") and "\n+++ " in stripped
    )


def _run_status(patch: str, final_answer: str) -> str:
    if patch.strip():
        return "patch_generated"
    if final_answer.startswith("blocked:"):
        return "blocked"
    return "no_patch"
