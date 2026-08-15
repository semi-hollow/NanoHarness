"""可恢复的 repeated matched benchmark campaign 用例。

阅读入口只有 ``RunBenchmarkCampaign.run_campaign``。它在每个运行槽位前后保存
``campaign.json``。普通 A/B campaign 可重试 running/failed 槽位；严格 Pass@1
campaign 则只继续未开始槽位，已经启动的轨迹会 fail closed。
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
    RETRYABLE_INFRASTRUCTURE_FAILURES,
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


@dataclass(frozen=True, kw_only=True)
class BenchmarkCampaignResult:
    """公开调用方真正需要的 campaign 状态和 artifact 位置。"""

    state: CampaignState
    campaign_dir: Path
    summary_path: Path
    report_path: Path
    published_bundle_dir: Path | None


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
    def run_campaign(
        self,
        request: BenchmarkCampaignRequest,
    ) -> BenchmarkCampaignResult:
        """已完成槽位幂等跳过，并按请求中冻结的 started-slot 策略恢复。"""

        # region 准备区（实现细节）：目录、源码、实验身份与可恢复状态
        # configuration_digest 同时绑定实验配置和源码身份；恢复只接受同一 digest，
        # 防止把不同代码或不同 Case 集的槽位拼进一个 Campaign 结论。
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
        variants_by_name = {variant.name: variant for variant in request.variants}
        # endregion 准备区结束

        # region 2. 交错实验：每个 run slot 都是可 checkpoint、可恢复的最小单元
        # record 已按预注册 ordinal 固定顺序；完成槽位幂等跳过，失败槽位保留原 ordinal，
        # 每次状态变化都立即 save_state，因此中断不会要求整批重跑。
        for campaign_run_slot in sorted(
            campaign_state.records,
            key=lambda campaign_run_slot: campaign_run_slot.ordinal,
        ):
            if campaign_run_slot.status == "completed":
                continue
            if not request.rerun_incomplete_slots and campaign_run_slot.attempts > 0:
                self._fail_closed_started_slot(
                    campaign_run_slot,
                    campaign_state,
                    campaign_dir,
                )
                continue
            retry_same_slot = True
            while retry_same_slot:
                # retry_same_slot 只由明确的基础设施重试分类返回，任务失败不会在同槽位刷到成功。
                self._mark_run_slot_started(
                    campaign_run_slot,
                    campaign_state,
                    campaign_dir,
                )
                try:
                    benchmark_request = self._build_run_slot_request(
                        request,
                        campaign_dir=campaign_dir,
                        campaign_run_slot=campaign_run_slot,
                        variant=variants_by_name[campaign_run_slot.variant],
                    )
                    benchmark_run = self._runner(benchmark_request)
                    retry_same_slot = self._record_run_slot_completion(
                        campaign_run_slot,
                        benchmark_run,
                        max_infrastructure_attempts=request.max_infrastructure_attempts,
                    )
                except Exception as exc:
                    campaign_run_slot.status = "failed"
                    campaign_run_slot.error = f"{type(exc).__name__}: {exc}"
                    retry_same_slot = False
                finally:
                    campaign_state.updated_at = self._now()
                    self._artifacts.save_state(campaign_dir, campaign_state)
        # endregion 2. 交错实验结束

        # region 3. 聚合发布：只消费已持久化槽位，不重新推断 Case 正确性
        campaign_state.status = (
            "completed"
            if all(
                campaign_run_slot.status == "completed"
                for campaign_run_slot in campaign_state.records
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
            published_bundle_dir=published_bundle_dir,
        )
        # endregion 3. 聚合发布结束

    # region 单槽位与恢复细节
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

    def _mark_run_slot_started(
        self,
        campaign_run_slot: CampaignRunRecord,
        campaign_state: CampaignState,
        campaign_dir: Path,
    ) -> None:
        """先保存 running 状态，使进程崩溃后该槽位可被识别并重试。"""

        campaign_run_slot.status = "running"
        campaign_run_slot.attempts += 1
        campaign_run_slot.error = ""
        campaign_state.status = "running"
        campaign_state.updated_at = self._now()
        self._artifacts.save_state(campaign_dir, campaign_state)

    def _fail_closed_started_slot(
        self,
        campaign_run_slot: CampaignRunRecord,
        campaign_state: CampaignState,
        campaign_dir: Path,
    ) -> None:
        """恢复时绝不为已启动的 Pass@1 槽位创建第二条 Agent 轨迹。"""

        previous_status = campaign_run_slot.status
        campaign_run_slot.status = "failed"
        campaign_run_slot.error = (
            "strict_pass_at_one_no_rerun: "
            f"previous_status={previous_status}; attempts={campaign_run_slot.attempts}"
        )
        campaign_state.status = "running"
        campaign_state.updated_at = self._now()
        self._artifacts.save_state(campaign_dir, campaign_state)

    def _build_run_slot_request(
        self,
        campaign_request: BenchmarkCampaignRequest,
        *,
        campaign_dir: Path,
        campaign_run_slot: CampaignRunRecord,
        variant: CampaignVariant,
    ) -> SwebenchRunRequest:
        """为一个 case/repetition/variant 槽位构造独立 benchmark 请求。"""

        # 每个槽位拥有独立目录，恢复时不会覆盖另一 case/repetition。
        slot_output_root = (
            campaign_dir
            / "runs"
            / safe_id(variant.name)
            / (
                f"r{campaign_run_slot.repetition:02d}-"
                f"{safe_id(campaign_run_slot.case_id)}"
            )
        )
        return replace(
            campaign_request.benchmark,
            limit=1,
            instance_ids=(campaign_run_slot.case_id,),
            output_root=str(slot_output_root),
            agent_mode="single",
            tool_routing_mode=variant.tool_routing_mode,
            skill_mode=variant.skill_mode,
            skill_names=variant.skill_names,
            skill_manifest_files=(),
            memory_root="",
            memory_namespace="",
            memory_max_chars=0,
        )

    def _record_run_slot_completion(
        self,
        campaign_run_slot: CampaignRunRecord,
        benchmark_run: BenchRunSummary,
        *,
        max_infrastructure_attempts: int,
    ) -> bool:
        """提交一次尝试；瞬时基础设施失败最多原位重试一次。"""

        campaign_run_slot.run_id = benchmark_run.run_id
        campaign_run_slot.run_dir = str(benchmark_run.output_dir)
        campaign_run_slot.scorecard_sha256 = self._artifacts.scorecard_sha256(
            benchmark_run.output_dir
        )
        campaign_run_slot.evidence = _extract_run_evidence(
            benchmark_run,
            self._artifacts.read_scorecard(benchmark_run.output_dir),
        )
        campaign_run_slot.error = ""
        failure_class = str(campaign_run_slot.evidence.get("failure_class") or "")
        if (
            failure_class in RETRYABLE_INFRASTRUCTURE_FAILURES
            and campaign_run_slot.attempts < max_infrastructure_attempts
        ):
            campaign_run_slot.attempt_history.append(
                {
                    "attempt": campaign_run_slot.attempts,
                    "run_id": campaign_run_slot.run_id,
                    "scorecard_sha256": campaign_run_slot.scorecard_sha256,
                    "evidence": dict(campaign_run_slot.evidence),
                }
            )
            campaign_run_slot.status = "retry_pending"
            campaign_run_slot.error = f"retry_scheduled:{failure_class}"
            return True
        campaign_run_slot.status = "completed"
        if failure_class in RETRYABLE_INFRASTRUCTURE_FAILURES:
            campaign_run_slot.evidence["infrastructure_retry_exhausted"] = True
        return False

    # endregion 单槽位与恢复细节结束


def _extract_run_evidence(
    benchmark_run: BenchRunSummary,
    scorecard_payload: dict[str, Any],
) -> dict[str, Any]:
    # 准备区：优先读取 scorecard；缺字段时才回退到本次 run 的 case result。
    scorecard_cases = (
        scorecard_payload.get("cases") if isinstance(scorecard_payload, dict) else None
    )
    scorecard_case = (
        scorecard_cases[0]
        if isinstance(scorecard_cases, list) and scorecard_cases
        else {}
    )
    if not isinstance(scorecard_case, dict):
        scorecard_case = {}
    benchmark_case_result = (
        benchmark_run.case_results[0] if benchmark_run.case_results else None
    )
    return {
        "status": str(
            scorecard_case.get("status")
            or (benchmark_case_result.status if benchmark_case_result else "unknown")
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
        "estimated_cost_usd": float(scorecard_case.get("estimated_cost_usd") or 0.0),
        "llm_latency_ms": int(scorecard_case.get("llm_latency_ms") or 0),
        "tool_calls": int(scorecard_case.get("tool_calls") or 0),
        "failed_tool_calls": int(scorecard_case.get("failed_tool_calls") or 0),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
