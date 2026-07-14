from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.bench.application.dependencies import BenchDependencies
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase, BenchCaseResult, BenchRunSummary
from agent_forge.evaluation.api import compare_runs, compare_variants, extract_run_metrics


class RunSwebench:

    def __init__(self, dependencies: BenchDependencies) -> None:
        self._deps = dependencies

    # 主要入口：下方定义承接该模块的核心调用。
    def execute(
        self,
        request: SwebenchRunRequest,
        *,
        run_id: str,
        layout: BenchRunLayout,
    ) -> BenchRunSummary:
        """按 case 执行、official evaluation、最终诊断和发布顺序运行评测。"""

        cases = self._deps.cases.load(request)
        summary = _new_summary(request, run_id, layout)
        predictions: list[dict[str, Any]] = []
        baseline_predictions: list[dict[str, Any]] = []
        baseline_by_case: dict[str, dict[str, Any]] = {}

        for case in cases:
            result = self._execute_case(case, request, layout)
            summary.case_results.append(result)
            predictions.append(
                self._deps.artifacts.prediction_for(
                    result,
                    provider=request.provider,
                    model=request.model,
                )
            )
            if request.direct_baseline:
                baseline_record = self._deps.baseline.predict(case, request)
                baseline_predictions.append(baseline_record)
                baseline_by_case[case.instance_id] = baseline_record

        self._deps.artifacts.write_predictions(
            summary,
            predictions,
            baseline_predictions,
        )
        if request.evaluate:
            self._deps.official_evaluator.evaluate(summary, request)

        for result in summary.case_results:
            self._deps.artifacts.finalize_case(result)
            stored_baseline = baseline_by_case.get(result.instance_id)
            if stored_baseline is not None:
                summary.variant_comparisons[result.instance_id] = compare_variants(
                    result.instance_id,
                    {
                        "direct_baseline": stored_baseline,
                        _agent_variant_name(summary.agent_mode): extract_run_metrics(
                            result.to_dict(),
                            self._deps.artifacts.read_json(
                                result.trace_path.parent / "usage.json"
                            ),
                        ),
                    },
                )

        self._deps.artifacts.publish_run(
            summary,
            predictions,
            baseline_predictions,
        )
        return summary

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
        case_root = layout.case_dir(case.instance_id)
        single_result = self._deps.executor.run(
            case,
            case_dir=case_root / "single",
            agent_mode="single",
            request=request,
        )
        multi_result = self._deps.executor.run(
            case,
            case_dir=case_root / "multi",
            agent_mode="multi",
            request=request,
        )
        comparison = compare_runs(
            case.instance_id,
            extract_run_metrics(
                single_result.to_dict(),
                self._deps.artifacts.read_json(
                    single_result.trace_path.parent / "usage.json"
                ),
            ),
            extract_run_metrics(
                multi_result.to_dict(),
                self._deps.artifacts.read_json(
                    multi_result.trace_path.parent / "usage.json"
                ),
                self._deps.artifacts.read_json(
                    multi_result.trace_path.parent
                    / "multi_agent"
                    / "multi_agent_summary.json"
                ),
            ),
        )
        self._deps.artifacts.write_comparison(comparison, case_root)
        combined_patch = case_root / "patch.diff"
        self._deps.artifacts.copy_patch(multi_result.patch_path, combined_patch)
        return _combined_result(
            case,
            single_result,
            multi_result,
            combined_patch,
        )


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
        baseline_predictions_path=layout.baseline_predictions_path,
        notes=[
            "Generated patches are not resolved-rate claims until the official SWE-bench harness evaluates them.",
            "Repo workspaces are under .agent_forge/runs so the main checkout stays clean.",
        ],
    )


def _combined_result(
    case: BenchCase,
    single: BenchCaseResult,
    multi: BenchCaseResult,
    patch_path: Path,
) -> BenchCaseResult:

    return BenchCaseResult(
        instance_id=case.instance_id,
        repo=case.repo,
        workspace=multi.workspace,
        trace_path=multi.trace_path,
        usage_report_path=multi.usage_report_path,
        patch_path=patch_path,
        status=multi.status,
        final_answer=multi.final_answer,
        patch_chars=multi.patch_chars,
        error=multi.error,
        evaluation_status=multi.evaluation_status,
        local_validation_status=multi.local_validation_status,
        local_validation_evidence=multi.local_validation_evidence,
        official_evaluation_status=multi.official_evaluation_status,
        official_evaluation_report_path=multi.official_evaluation_report_path,
        official_evaluation_detail=multi.official_evaluation_detail,
        failure_class=multi.failure_class or single.failure_class,
        diagnosis=multi.diagnosis or single.diagnosis,
        diagnosis_evidence=[
            *single.diagnosis_evidence[:2],
            *multi.diagnosis_evidence[:2],
        ],
        next_actions=multi.next_actions or single.next_actions,
    )


def _agent_variant_name(agent_mode: str) -> str:
    if agent_mode in {"multi", "compare"}:
        return "multi_agent"
    return "agent_runtime"
