"""可恢复的 repeated matched benchmark campaign 用例。

阅读入口只有 ``RunBenchmarkCampaign.execute``。它在每个运行槽位前后保存
``campaign.json``，因此进程中断后可以跳过已完成槽位并重试 running/failed 槽位。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent_forge.bench.domain.campaign import (
    BenchmarkCampaignRequest,
    CampaignRunRecord,
    CampaignState,
    CampaignVariant,
    build_campaign_records,
    campaign_config_digest,
    summarize_campaign,
)
from agent_forge.bench.domain.config import SwebenchRunRequest, safe_id
from agent_forge.bench.domain.models import BenchRunSummary
from agent_forge.bench.ports import (
    BenchmarkRunnerPort,
    CampaignArtifactPort,
    SourceIdentityPort,
)


@dataclass(frozen=True)
class BenchmarkCampaignResult:
    """公开调用方真正需要的 campaign 状态和 artifact 位置。"""

    state: CampaignState
    campaign_dir: Path
    summary_path: Path
    report_path: Path
    public_dir: Path | None


class RunBenchmarkCampaign:
    """固定实验身份，交错执行变体，并持续发布可恢复状态。"""

    def __init__(
        self,
        runner: BenchmarkRunnerPort,
        artifacts: CampaignArtifactPort,
        source_identity: SourceIdentityPort,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._runner = runner
        self._artifacts = artifacts
        self._source_identity = source_identity
        self._now = now or _utc_now

    # 主要入口：运行或恢复整个 campaign；其余方法只做单槽位适配。
    def execute(self, request: BenchmarkCampaignRequest) -> BenchmarkCampaignResult:
        """已完成槽位幂等跳过；失败槽位在下一次相同配置恢复时重试。"""

        # region 准备区（首遍可折叠）：目录、源码、实验身份与可恢复状态
        campaign_dir = self._artifacts.campaign_dir(
            request.output_root,
            request.campaign_id,
        )
        source_identity = self._source_identity.read()
        if source_identity.get("dirty") and not request.allow_dirty:
            raise ValueError(
                "benchmark campaign requires a clean git source; commit changes or pass "
                "--allow-dirty and accept the weaker reproducibility boundary"
            )
        experiment_identity = request.identity()
        configuration_digest = campaign_config_digest(
            experiment_identity,
            source_identity,
        )
        campaign_state = self._load_or_create_state(
            request,
            campaign_dir=campaign_dir,
            experiment_identity=experiment_identity,
            source_identity=source_identity,
            configuration_digest=configuration_digest,
        )
        variants_by_name = {
            variant.name: variant for variant in request.variants
        }
        # endregion 准备区结束

        # 执行区：每个 record 是一个可单独 checkpoint、可恢复的实验槽位。
        for record in sorted(
            campaign_state.records,
            key=lambda item: item.ordinal,
        ):
            if record.status == "completed":
                continue
            self._start_record(record, campaign_state, campaign_dir)
            try:
                benchmark_request = self._benchmark_request(
                    request,
                    campaign_dir=campaign_dir,
                    record=record,
                    variant=variants_by_name[record.variant],
                )
                benchmark_run = self._runner(benchmark_request)
                self._complete_record(record, benchmark_run)
            except Exception as exc:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
            finally:
                campaign_state.updated_at = self._now()
                self._artifacts.save_state(campaign_dir, campaign_state)

        # 收口区：聚合只消费已经持久化的槽位事实，不重新推断 case 结果。
        campaign_state.status = (
            "completed"
            if all(
                record.status == "completed"
                for record in campaign_state.records
            )
            else "completed_with_failures"
        )
        campaign_state.updated_at = self._now()
        self._artifacts.save_state(campaign_dir, campaign_state)
        campaign_summary = summarize_campaign(campaign_state)
        summary_path, report_path = self._artifacts.write_final_artifacts(
            campaign_dir,
            campaign_state,
            campaign_summary,
        )
        published_bundle_dir = (
            self._artifacts.publish_public_bundle(
                request.publish_root,
                campaign_dir,
                campaign_state,
                campaign_summary,
            )
            if request.publish_root and campaign_state.status == "completed"
            else None
        )
        self._artifacts.update_latest_pointer(campaign_dir)
        return BenchmarkCampaignResult(
            state=campaign_state,
            campaign_dir=campaign_dir,
            summary_path=summary_path,
            report_path=report_path,
            public_dir=published_bundle_dir,
        )

    # region 单槽位与恢复细节（首次阅读可折叠）
    def _load_or_create_state(
        self,
        request: BenchmarkCampaignRequest,
        *,
        campaign_dir: Path,
        experiment_identity: dict[str, Any],
        source_identity: dict[str, Any],
        configuration_digest: str,
    ) -> CampaignState:
        existing_campaign_state = self._artifacts.load_state(campaign_dir)
        if existing_campaign_state is not None:
            if not request.resume:
                raise ValueError(
                    f"campaign already exists and resume is disabled: {campaign_dir}"
                )
            if existing_campaign_state.config_digest != configuration_digest:
                raise ValueError(
                    "campaign config or source revision changed; use a new campaign_id"
                )
            return existing_campaign_state
        created_at = self._now()
        new_campaign_state = CampaignState(
            campaign_id=request.campaign_id,
            config_digest=configuration_digest,
            config=experiment_identity,
            source=source_identity,
            created_at=created_at,
            updated_at=created_at,
            records=build_campaign_records(request),
        )
        self._artifacts.save_state(campaign_dir, new_campaign_state)
        return new_campaign_state

    def _start_record(
        self,
        record: CampaignRunRecord,
        campaign_state: CampaignState,
        campaign_dir: Path,
    ) -> None:
        record.status = "running"
        record.attempts += 1
        record.error = ""
        campaign_state.status = "running"
        campaign_state.updated_at = self._now()
        self._artifacts.save_state(campaign_dir, campaign_state)

    def _benchmark_request(
        self,
        campaign: BenchmarkCampaignRequest,
        *,
        campaign_dir: Path,
        record: CampaignRunRecord,
        variant: CampaignVariant,
    ) -> SwebenchRunRequest:
        # 每个槽位拥有独立目录，恢复时不会覆盖另一 case/repetition。
        slot_output_root = (
            campaign_dir
            / "runs"
            / safe_id(variant.name)
            / f"r{record.repetition:02d}-{safe_id(record.case_id)}"
        )
        return replace(
            campaign.benchmark,
            limit=1,
            instance_ids=(record.case_id,),
            output_root=str(slot_output_root),
            direct_baseline=False,
            agent_mode="single",
            tool_routing_mode=variant.tool_routing_mode,
            skill_mode=variant.skill_mode,
            skill_names=variant.skill_names,
            skill_manifest_files=(),
            memory_root="",
            memory_namespace="",
            memory_recall_limit=0,
        )

    def _complete_record(
        self,
        record: CampaignRunRecord,
        benchmark_run: BenchRunSummary,
    ) -> None:
        record.status = "completed"
        record.run_id = benchmark_run.run_id
        record.run_dir = str(benchmark_run.output_dir)
        record.scorecard_sha256 = self._artifacts.scorecard_sha256(
            benchmark_run.output_dir
        )
        record.evidence = _extract_run_evidence(
            benchmark_run,
            self._artifacts.read_scorecard(benchmark_run.output_dir),
        )
        record.error = ""
    # endregion 单槽位与恢复细节结束


def _extract_run_evidence(
    benchmark_run: BenchRunSummary,
    scorecard_payload: dict[str, Any],
) -> dict[str, Any]:
    # 准备区：优先读取 scorecard；缺字段时才回退到本次 run 的 case result。
    scorecard_cases = (
        scorecard_payload.get("cases")
        if isinstance(scorecard_payload, dict)
        else None
    )
    scorecard_case = (
        scorecard_cases[0]
        if isinstance(scorecard_cases, list) and scorecard_cases
        else {}
    )
    if not isinstance(scorecard_case, dict):
        scorecard_case = {}
    benchmark_case_result = (
        benchmark_run.case_results[0]
        if benchmark_run.case_results
        else None
    )
    return {
        "status": str(
            scorecard_case.get("status")
            or (
                benchmark_case_result.status
                if benchmark_case_result
                else "unknown"
            )
        ),
        "patch_generated": bool(
            scorecard_case.get("patch_generated")
            or (
                benchmark_case_result is not None
                and benchmark_case_result.patch_chars > 0
            )
        ),
        "patch_chars": int(
            scorecard_case.get("patch_chars")
            or (
                benchmark_case_result.patch_chars
                if benchmark_case_result is not None
                else 0
            )
        ),
        "local_validation_status": str(
            scorecard_case.get("local_validation_status")
            or (
                benchmark_case_result.local_validation_status
                if benchmark_case_result
                else "not_run"
            )
        ),
        "official_evaluation_status": str(
            scorecard_case.get("official_evaluation_status")
            or (
                benchmark_case_result.official_evaluation_status
                if benchmark_case_result
                else "not_evaluated"
            )
        ),
        "failure_class": str(
            scorecard_case.get("failure_class")
            or (
                benchmark_case_result.failure_class
                if benchmark_case_result
                else "unclassified"
            )
        ),
        "total_tokens": int(scorecard_case.get("total_tokens") or 0),
        "estimated_cost_usd": float(
            scorecard_case.get("estimated_cost_usd") or 0.0
        ),
        "llm_latency_ms": int(scorecard_case.get("llm_latency_ms") or 0),
        "tool_calls": int(scorecard_case.get("tool_calls") or 0),
        "failed_tool_calls": int(
            scorecard_case.get("failed_tool_calls") or 0
        ),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
