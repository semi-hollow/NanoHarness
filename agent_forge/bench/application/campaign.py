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

        campaign_dir = self._artifacts.campaign_dir(
            request.output_root,
            request.campaign_id,
        )
        source = self._source_identity.read()
        if source.get("dirty") and not request.allow_dirty:
            raise ValueError(
                "benchmark campaign requires a clean git source; commit changes or pass "
                "--allow-dirty and accept the weaker reproducibility boundary"
            )
        identity = request.identity()
        digest = campaign_config_digest(identity, source)
        state = self._load_or_create_state(
            request,
            campaign_dir=campaign_dir,
            identity=identity,
            source=source,
            digest=digest,
        )
        variants = {variant.name: variant for variant in request.variants}

        for record in sorted(state.records, key=lambda item: item.ordinal):
            if record.status == "completed":
                continue
            self._start_record(record, state, campaign_dir)
            try:
                benchmark_request = self._benchmark_request(
                    request,
                    campaign_dir=campaign_dir,
                    record=record,
                    variant=variants[record.variant],
                )
                run = self._runner(benchmark_request)
                self._complete_record(record, run)
            except Exception as exc:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
            finally:
                state.updated_at = self._now()
                self._artifacts.save_state(campaign_dir, state)

        state.status = (
            "completed"
            if all(record.status == "completed" for record in state.records)
            else "completed_with_failures"
        )
        state.updated_at = self._now()
        self._artifacts.save_state(campaign_dir, state)
        summary = summarize_campaign(state)
        summary_path, report_path = self._artifacts.write_final_artifacts(
            campaign_dir,
            state,
            summary,
        )
        public_dir = (
            self._artifacts.publish_public_bundle(
                request.publish_root,
                campaign_dir,
                state,
                summary,
            )
            if request.publish_root and state.status == "completed"
            else None
        )
        self._artifacts.update_latest_pointer(campaign_dir)
        return BenchmarkCampaignResult(
            state=state,
            campaign_dir=campaign_dir,
            summary_path=summary_path,
            report_path=report_path,
            public_dir=public_dir,
        )

    def _load_or_create_state(
        self,
        request: BenchmarkCampaignRequest,
        *,
        campaign_dir: Path,
        identity: dict[str, Any],
        source: dict[str, Any],
        digest: str,
    ) -> CampaignState:
        existing = self._artifacts.load_state(campaign_dir)
        if existing is not None:
            if not request.resume:
                raise ValueError(
                    f"campaign already exists and resume is disabled: {campaign_dir}"
                )
            if existing.config_digest != digest:
                raise ValueError(
                    "campaign config or source revision changed; use a new campaign_id"
                )
            return existing
        created_at = self._now()
        state = CampaignState(
            campaign_id=request.campaign_id,
            config_digest=digest,
            config=identity,
            source=source,
            created_at=created_at,
            updated_at=created_at,
            records=build_campaign_records(request),
        )
        self._artifacts.save_state(campaign_dir, state)
        return state

    def _start_record(
        self,
        record: CampaignRunRecord,
        state: CampaignState,
        campaign_dir: Path,
    ) -> None:
        record.status = "running"
        record.attempts += 1
        record.error = ""
        state.status = "running"
        state.updated_at = self._now()
        self._artifacts.save_state(campaign_dir, state)

    def _benchmark_request(
        self,
        campaign: BenchmarkCampaignRequest,
        *,
        campaign_dir: Path,
        record: CampaignRunRecord,
        variant: CampaignVariant,
    ) -> SwebenchRunRequest:
        run_root = (
            campaign_dir
            / "runs"
            / safe_id(variant.name)
            / f"r{record.repetition:02d}-{safe_id(record.case_id)}"
        )
        return replace(
            campaign.benchmark,
            limit=1,
            instance_ids=(record.case_id,),
            output_root=str(run_root),
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
        run: BenchRunSummary,
    ) -> None:
        record.status = "completed"
        record.run_id = run.run_id
        record.run_dir = str(run.output_dir)
        record.scorecard_sha256 = self._artifacts.scorecard_sha256(run.output_dir)
        record.evidence = _extract_run_evidence(
            run,
            self._artifacts.read_scorecard(run.output_dir),
        )
        record.error = ""


def _extract_run_evidence(
    run: BenchRunSummary,
    scorecard: dict[str, Any],
) -> dict[str, Any]:
    cases = scorecard.get("cases") if isinstance(scorecard, dict) else None
    case = cases[0] if isinstance(cases, list) and cases else {}
    if not isinstance(case, dict):
        case = {}
    result = run.case_results[0] if run.case_results else None
    return {
        "status": str(case.get("status") or (result.status if result else "unknown")),
        "patch_generated": bool(
            case.get("patch_generated")
            or (result is not None and result.patch_chars > 0)
        ),
        "patch_chars": int(
            case.get("patch_chars")
            or (result.patch_chars if result is not None else 0)
        ),
        "local_validation_status": str(
            case.get("local_validation_status")
            or (result.local_validation_status if result else "not_run")
        ),
        "official_evaluation_status": str(
            case.get("official_evaluation_status")
            or (result.official_evaluation_status if result else "not_evaluated")
        ),
        "failure_class": str(
            case.get("failure_class")
            or (result.failure_class if result else "unclassified")
        ),
        "total_tokens": int(case.get("total_tokens") or 0),
        "estimated_cost_usd": float(case.get("estimated_cost_usd") or 0.0),
        "llm_latency_ms": int(case.get("llm_latency_ms") or 0),
        "tool_calls": int(case.get("tool_calls") or 0),
        "failed_tool_calls": int(case.get("failed_tool_calls") or 0),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
