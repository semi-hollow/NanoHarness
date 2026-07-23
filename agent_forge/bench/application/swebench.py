from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agent_forge.bench.application.dependencies import BenchDependencies
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult, BenchRunSummary
from agent_forge.evaluation.api import compare_runs, compare_variants, extract_run_metrics


class RunSwebench:
    """SWE-bench 的阶段编排器；第一遍只读 ``execute``。"""

    def __init__(self, dependencies: BenchDependencies) -> None:
        self._deps = dependencies

    # 主要入口：编排 case 执行、官方评测、最终诊断、对照与发布。
    def execute(
        self,
        request: SwebenchRunRequest,
        *,
        run_id: str,
        layout: BenchRunLayout,
    ) -> BenchRunSummary:
        """按 case 执行、official evaluation、最终诊断和发布顺序运行评测。"""

        # region 准备区（首遍可折叠）：固定输入与跨 case 累积容器
        _validate_frozen_inputs(request)
        selected_cases = self._deps.cases.load(request)
        run_summary = _new_summary(request, run_id, layout)
        # Agent 预测：official evaluator 的正式输入，顺序与 selected_cases 一致。
        agent_prediction_records: list[dict[str, Any]] = []
        # 直接模型基线：与 Agent 预测分文件发布，避免混成同一种运行结果。
        direct_baseline_prediction_records: list[dict[str, Any]] = []
        # 基线索引：final diagnosis 时按 instance_id 做 O(1) 对照查找。
        direct_baseline_by_instance_id: dict[str, dict[str, Any]] = {}
        # endregion 准备区结束

        # 执行区：每个 case 先跑 Harness，再按需运行无 Harness 的直接模型基线。
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
            if request.direct_baseline:
                generated_baseline_prediction = self._deps.baseline.predict(
                    case,
                    request,
                )
                direct_baseline_prediction_records.append(
                    generated_baseline_prediction
                )
                direct_baseline_by_instance_id[case.instance_id] = (
                    generated_baseline_prediction
                )

        # 评测区：先发布 evaluator 输入，再由 official harness 写回最终判定。
        self._deps.artifacts.write_predictions(
            run_summary,
            agent_prediction_records,
            direct_baseline_prediction_records,
        )
        if request.evaluate:
            self._deps.official_evaluator.evaluate(run_summary, request)

        # 收口区：final diagnosis 完成后，才能生成 baseline 对照和最终报告。
        for case_result in run_summary.case_results:
            self._deps.artifacts.finalize_case(case_result)
            matching_baseline_prediction = direct_baseline_by_instance_id.get(
                case_result.instance_id
            )
            if matching_baseline_prediction is not None:
                run_summary.variant_comparisons[
                    case_result.instance_id
                ] = compare_variants(
                    case_result.instance_id,
                    {
                        "direct_baseline": matching_baseline_prediction,
                        _agent_variant_name(
                            run_summary.agent_mode
                        ): extract_run_metrics(
                            case_result.to_dict(),
                            self._deps.artifacts.read_json(
                                case_result.trace_path.parent / "usage.json"
                            ),
                        ),
                    },
                )

        _verify_frozen_inputs(request, run_summary)
        self._deps.artifacts.publish_run(
            run_summary,
            agent_prediction_records,
            direct_baseline_prediction_records,
        )
        return run_summary

    # region 单 case 执行细节（首次阅读可折叠）
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
        # region 路径准备（可折叠）：single/multi 共用 case 根，各自独立运行
        case_output_root = layout.case_dir(case.instance_id)
        single_agent_run_dir = case_output_root / "single"
        multi_agent_run_dir = case_output_root / "multi"
        selected_candidate_patch_path = case_output_root / "patch.diff"
        # endregion 路径准备结束

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
        self._deps.artifacts.copy_patch(
            multi_agent_result.patch_path,
            selected_candidate_patch_path,
        )
        return _combined_result(
            case,
            single_agent_result,
            multi_agent_result,
            selected_candidate_patch_path,
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
        memory_namespace=request.memory_namespace or "swebench:<instance_id>",
        memory_recall_limit=request.memory_recall_limit,
        memory_snapshot_sha256=_directory_sha256(request.memory_root),
        baseline_predictions_path=layout.baseline_predictions_path,
        notes=[
            "Generated patches are not resolved-rate claims until the official SWE-bench harness evaluates them.",
            "Repo workspaces are under .agent_forge/runs so the main checkout stays clean.",
        ],
    )


def _combined_result(
    case: BenchCase,
    single_agent_result: BenchCaseResult,
    multi_agent_result: BenchCaseResult,
    selected_candidate_patch_path: Path,
) -> BenchCaseResult:
    return BenchCaseResult(
        instance_id=case.instance_id,
        repo=case.repo,
        workspace=multi_agent_result.workspace,
        trace_path=multi_agent_result.trace_path,
        usage_report_path=multi_agent_result.usage_report_path,
        patch_path=selected_candidate_patch_path,
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
            multi_agent_result.failure_class
            or single_agent_result.failure_class
        ),
        diagnosis=(
            multi_agent_result.diagnosis
            or single_agent_result.diagnosis
        ),
        diagnosis_evidence=[
            *single_agent_result.diagnosis_evidence[:2],
            *multi_agent_result.diagnosis_evidence[:2],
        ],
        next_actions=(
            multi_agent_result.next_actions
            or single_agent_result.next_actions
        ),
    )


def _agent_variant_name(agent_mode: str) -> str:
    if agent_mode in {"multi", "compare"}:
        return "multi_agent"
    return "agent_runtime"


def _directory_sha256(root: str) -> str:
    """固定长期记忆输入的内容指纹，供配对实验检查漂移。"""

    if not root:
        return "disabled"
    memory_root_path = Path(root).expanduser()
    if not memory_root_path.is_dir():
        return "missing"
    content_digest = hashlib.sha256()
    memory_files = sorted(
        candidate
        for candidate in memory_root_path.rglob("*")
        if candidate.is_file()
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
    resolved_manifest_paths = [
        Path(raw_path).expanduser() for raw_path in paths
    ]
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
        raise ValueError(
            "memory_root is required when memory_recall_limit is positive"
        )
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
    current_skill_manifest_sha256 = _files_sha256(
        request.skill_manifest_files
    )
    if current_skill_manifest_sha256 != summary.skill_manifest_sha256:
        raise RuntimeError("skill manifest changed during benchmark run")
