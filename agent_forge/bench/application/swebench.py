from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agent_forge.bench.application.dependencies import BenchDependencies
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult, BenchRunSummary
from agent_forge.evaluation.api import compare_runs, extract_run_metrics


class RunSwebench:
    """SWE-bench 的阶段编排器；``run_benchmark`` 是公开主入口。"""

    def __init__(self, dependencies: BenchDependencies) -> None:
        self._deps = dependencies

    # 主要入口：编排 case 执行、官方评测、最终诊断、对照与发布。
    def run_benchmark(
        self,
        request: SwebenchRunRequest,
        *,
        run_id: str,
        layout: BenchRunLayout,
    ) -> BenchRunSummary:
        """按 case 执行、official evaluation、最终诊断和发布顺序运行评测。"""

        # region 准备区（实现细节）：固定输入与跨 case 累积容器
        _validate_frozen_inputs(request)
        selected_cases = self._deps.cases.load(request)
        run_summary = _new_summary(request, run_id, layout)
        # Agent 预测：official evaluator 的正式输入，顺序与 selected_cases 一致。
        agent_prediction_records: list[dict[str, Any]] = []
        # endregion 准备区结束

        # region 2. Case 执行：运行 Harness，并生成 official evaluator 所需预测
        # 每个 Case 先保留独立 Runtime 结果，再提取官方 harness 需要的最小预测记录；
        # 此处不判定 solved，避免 Agent 自述或 candidate diff 越级成为 correctness。
        for case in selected_cases:
            case_result = self._execute_case(case, request, layout)
            run_summary.case_results.append(case_result)
            agent_prediction_records.append(
                self._deps.artifacts.prediction_for(
                    case_result,
                    provider=request.provider,
                    model=request.model,
                )
            )
        # endregion 2. Case 执行结束

        # region 3. Official evaluation：先冻结预测输入，再由官方 harness 写回判定
        self._deps.artifacts.write_predictions(
            run_summary,
            agent_prediction_records,
        )
        if request.evaluate:
            self._deps.official_evaluator.evaluate(run_summary, request)
        # endregion 3. Official evaluation结束

        # region 4. 诊断与发布：final diagnosis 后才能生成对照和最终报告
        # finalize_case 必须在 official evaluator 写回后调用，确保 Taxonomy 能看见权威结果；
        # 后续 renderer 只读最终 Case，不重新猜测 correctness。
        for case_result in run_summary.case_results:
            self._deps.artifacts.finalize_case(case_result)

        _verify_frozen_inputs(request, run_summary)
        self._deps.artifacts.publish_run(
            run_summary,
            agent_prediction_records,
        )
        return run_summary
        # endregion 4. 诊断与发布结束

    # region 单 case 执行细节
    def _execute_case(
        self,
        case: BenchCase,
        request: SwebenchRunRequest,
        layout: BenchRunLayout,
    ) -> BenchCaseResult:
        if request.agent_mode != "compare":
            return self._deps.executor.run(
                case,
                case_dir=layout.case_dir(case.instance_id),
                agent_mode=request.agent_mode,
                request=request,
            )
        return self._execute_comparison(case, request, layout)

    def _execute_comparison(
        self,
        case: BenchCase,
        request: SwebenchRunRequest,
        layout: BenchRunLayout,
    ) -> BenchCaseResult:
        """独立执行 single/multi 两个变体，再生成同 Case 的结构化对照。"""

        # region 路径准备（可折叠）：single/multi 共用 case 根，各自独立运行
        case_output_root = layout.case_dir(case.instance_id)
        single_agent_run_dir = case_output_root / "single"
        multi_agent_run_dir = case_output_root / "multi"
        selected_candidate_diff_path = case_output_root / "candidate_changes.diff"
        # endregion 路径准备结束

        # region 2. 变体执行：相同 Case 分别进入独立目录，防止状态互相污染
        # single 与 multi 使用相同 Case/请求，但拥有独立 workspace、trace 和 artifact；
        # 这保证差异来自 Runtime 变体，而不是前一个变体留下的文件状态。
        single_agent_result = self._deps.executor.run(
            case,
            case_dir=single_agent_run_dir,
            agent_mode="single",
            request=request,
        )
        multi_agent_result = self._deps.executor.run(
            case,
            case_dir=multi_agent_run_dir,
            agent_mode="multi",
            request=request,
        )
        # endregion 2. 变体执行结束

        # region 3. 指标与候选收口：比较运行事实，并显式选择 multi candidate
        # compare_runs 生成解释性指标；当前 compare 模式的交付候选明确取 multi diff，
        # 不能根据对照分数偷偷切换候选，否则实验与实际输出会失去对应关系。
        run_comparison = compare_runs(
            case.instance_id,
            extract_run_metrics(
                single_agent_result.to_dict(),
                self._deps.artifacts.read_json(
                    single_agent_result.trace_path.parent / "usage.json"
                ),
            ),
            extract_run_metrics(
                multi_agent_result.to_dict(),
                self._deps.artifacts.read_json(
                    multi_agent_result.trace_path.parent / "usage.json"
                ),
                self._deps.artifacts.read_json(
                    multi_agent_result.trace_path.parent
                    / "multi_agent"
                    / "multi_agent_summary.json"
                ),
            ),
        )
        self._deps.artifacts.write_comparison(run_comparison, case_output_root)
        self._deps.artifacts.copy_candidate_diff(
            multi_agent_result.candidate_diff_path,
            selected_candidate_diff_path,
        )
        # endregion 3. 指标与候选收口结束
        return _combined_result(
            case,
            single_agent_result,
            multi_agent_result,
            selected_candidate_diff_path,
        )

    # endregion 单 case 执行细节结束


def _new_summary(
    request: SwebenchRunRequest,
    run_id: str,
    layout: BenchRunLayout,
) -> BenchRunSummary:
    return BenchRunSummary(
        run_id=run_id,
        dataset_name=request.dataset_name,
        split=request.split,
        provider=request.provider,
        model=request.model or "",
        temperature=request.temperature,
        thinking_mode=request.thinking_mode,
        reasoning_effort=request.reasoning_effort,
        output_dir=layout.output_dir,
        predictions_path=layout.predictions_path,
        agent_mode=request.agent_mode,
        profile=request.profile if request.agent_mode in {"multi", "compare"} else "",
        max_revision_rounds=(
            request.max_revision_rounds
            if request.agent_mode in {"multi", "compare"}
            else 0
        ),
        tool_routing_mode=request.tool_routing_mode,
        skill_mode=request.skill_mode,
        skill_names=list(request.skill_names),
        skill_manifest_sha256=_files_sha256(request.skill_manifest_files),
        execution_mode=request.execution_mode,
        network_policy=request.network_policy,
        keep_worktree=request.keep_worktree,
        container_runtime=request.container_runtime,
        container_image=request.container_image,
        container_cpus=request.container_cpus,
        container_memory=request.container_memory,
        container_pids_limit=request.container_pids_limit,
        container_read_only=request.container_read_only,
        max_steps=request.max_steps,
        max_context_chars=request.max_context_chars,
        max_prompt_tokens=request.max_prompt_tokens,
        reserved_output_tokens=request.reserved_output_tokens,
        max_tool_calls_per_turn=request.max_tool_calls_per_turn,
        cost_budget_usd=request.cost_budget_usd,
        timeout_seconds=request.timeout_seconds,
        model_request_timeout_seconds=request.model_request_timeout_seconds,
        model_request_max_attempts=request.model_request_max_attempts,
        tool_execution_timeout_seconds=request.tool_execution_timeout_seconds,
        memory_namespace=request.memory_namespace or "swebench:<instance_id>",
        memory_recall_limit=request.memory_recall_limit,
        memory_snapshot_sha256=_directory_sha256(request.memory_root),
        official_namespace=(
            "" if request.namespace_empty else request.official_namespace
        ),
        notes=[
            "Generated patches are not resolved-rate claims until the official SWE-bench harness evaluates them.",
            "Repo workspaces are under .agent_forge/runs so the main checkout stays clean.",
        ],
    )


def _combined_result(
    case: BenchCase,
    single_agent_result: BenchCaseResult,
    multi_agent_result: BenchCaseResult,
    selected_candidate_diff_path: Path,
) -> BenchCaseResult:
    return BenchCaseResult(
        instance_id=case.instance_id,
        repo=case.repo,
        workspace=multi_agent_result.workspace,
        trace_path=multi_agent_result.trace_path,
        usage_report_path=multi_agent_result.usage_report_path,
        candidate_diff_path=selected_candidate_diff_path,
        status=multi_agent_result.status,
        final_answer=multi_agent_result.final_answer,
        patch_chars=multi_agent_result.patch_chars,
        error=multi_agent_result.error,
        evaluation_status=multi_agent_result.evaluation_status,
        local_validation_status=multi_agent_result.local_validation_status,
        local_validation_evidence=multi_agent_result.local_validation_evidence,
        official_evaluation_status=multi_agent_result.official_evaluation_status,
        official_evaluation_report_path=(
            multi_agent_result.official_evaluation_report_path
        ),
        official_evaluation_detail=multi_agent_result.official_evaluation_detail,
        failure_class=(
            multi_agent_result.failure_class or single_agent_result.failure_class
        ),
        diagnosis=(multi_agent_result.diagnosis or single_agent_result.diagnosis),
        diagnosis_evidence=[
            *single_agent_result.diagnosis_evidence[:2],
            *multi_agent_result.diagnosis_evidence[:2],
        ],
        next_actions=(
            multi_agent_result.next_actions or single_agent_result.next_actions
        ),
    )


def _directory_sha256(root: str) -> str:
    """固定长期记忆输入的内容指纹，供配对实验检查漂移。"""

    if not root:
        return "disabled"
    memory_root_path = Path(root).expanduser()
    if not memory_root_path.is_dir():
        return "missing"
    content_digest = hashlib.sha256()
    memory_files = sorted(
        candidate for candidate in memory_root_path.rglob("*") if candidate.is_file()
    )
    for memory_file in memory_files:
        content_digest.update(
            str(memory_file.relative_to(memory_root_path)).encode("utf-8")
        )
        content_digest.update(b"\0")
        content_digest.update(memory_file.read_bytes())
        content_digest.update(b"\0")
    return content_digest.hexdigest()


def _files_sha256(paths: tuple[str, ...]) -> str:
    """记录 Skill manifest 的实际内容，而不只比较显示名称。"""

    if not paths:
        return "builtins_only"
    content_digest = hashlib.sha256()
    resolved_manifest_paths = [Path(raw_path).expanduser() for raw_path in paths]
    for manifest_path in sorted(
        resolved_manifest_paths,
        key=lambda candidate: (candidate.name, str(candidate)),
    ):
        content_digest.update(manifest_path.name.encode("utf-8"))
        content_digest.update(b"\0")
        if not manifest_path.is_file():
            content_digest.update(b"missing")
        else:
            content_digest.update(manifest_path.read_bytes())
        content_digest.update(b"\0")
    return content_digest.hexdigest()


def _validate_frozen_inputs(request: SwebenchRunRequest) -> None:
    """启用 Memory 召回时拒绝缺失快照，避免跑出无效 treatment。"""

    if request.memory_recall_limit <= 0:
        return
    if not request.memory_root:
        raise ValueError("memory_root is required when memory_recall_limit is positive")
    if not Path(request.memory_root).expanduser().is_dir():
        raise ValueError("memory_root must point to an existing frozen directory")


def _verify_frozen_inputs(
    request: SwebenchRunRequest,
    summary: BenchRunSummary,
) -> None:
    """运行结束时再次校验实验输入，检测外部并发修改。"""

    current_memory_snapshot_sha256 = _directory_sha256(request.memory_root)
    if current_memory_snapshot_sha256 != summary.memory_snapshot_sha256:
        raise RuntimeError("long-term memory snapshot changed during benchmark run")
    current_skill_manifest_sha256 = _files_sha256(request.skill_manifest_files)
    if current_skill_manifest_sha256 != summary.skill_manifest_sha256:
        raise RuntimeError("skill manifest changed during benchmark run")
