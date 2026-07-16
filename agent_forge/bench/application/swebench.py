from __future__ import annotations

import hashlib
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

        _validate_frozen_inputs(request)
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

        _verify_frozen_inputs(request, summary)
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
        temperature=request.temperature,
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


def _directory_sha256(root: str) -> str:
    """固定长期记忆输入的内容指纹，供配对实验检查漂移。"""

    if not root:
        return "disabled"
    path = Path(root).expanduser()
    if not path.is_dir():
        return "missing"
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _files_sha256(paths: tuple[str, ...]) -> str:
    """记录 Skill manifest 的实际内容，而不只比较显示名称。"""

    if not paths:
        return "builtins_only"
    digest = hashlib.sha256()
    resolved_paths = [Path(raw_path).expanduser() for raw_path in paths]
    for path in sorted(resolved_paths, key=lambda item: (item.name, str(item))):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        if not path.is_file():
            digest.update(b"missing")
        else:
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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

    memory_hash = _directory_sha256(request.memory_root)
    if memory_hash != summary.memory_snapshot_sha256:
        raise RuntimeError("long-term memory snapshot changed during benchmark run")
    skill_hash = _files_sha256(request.skill_manifest_files)
    if skill_hash != summary.skill_manifest_sha256:
        raise RuntimeError("skill manifest changed during benchmark run")
