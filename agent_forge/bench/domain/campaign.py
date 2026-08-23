"""重复 benchmark campaign 的稳定数据契约与纯聚合逻辑。

阅读入口：先看 ``BenchmarkCampaignRequest`` 理解实验身份，再看
``build_campaign_records`` 理解交错执行顺序，最后看 ``summarize_campaign``
理解哪些数字可以形成质量结论。这里不调用模型、不读写文件。

折叠导航：1 输入与状态契约；2 预注册槽位；3 Variant 汇总；4 配对视图。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent_forge.bench.domain.cohort import CohortSelection
from agent_forge.bench.domain.config import SwebenchRunRequest, safe_id


CAMPAIGN_SCHEMA_VERSION = 1
OFFICIAL_DECIDED = {"official_resolved", "official_eval_failed"}
RETRYABLE_INFRASTRUCTURE_FAILURES = frozenset(
    {
        "provider_transport_error",
        "official_eval_error",
        "runner_or_environment_error",
    }
)
_CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


# region 1. 输入与状态契约：Variant、Request、Run Record、Campaign checkpoint
# 核心数据：一个被比较的 Runtime preset，只描述主动变化的实验因子。
@dataclass(frozen=True)
class CampaignVariant:
    """同一正式 Runtime 的一个显式配置变体。

    ``name`` 是 artifact 中的稳定标识；``label`` 与 ``description`` 面向报告；
    其余字段会被转换成正式 ``SwebenchRunRequest``，不会走另一套模拟执行器。
    """

    name: str
    label: str
    description: str
    tool_routing_mode: str
    skill_mode: str
    skill_names: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "tool_routing_mode": self.tool_routing_mode,
            "skill_mode": self.skill_mode,
            "skill_names": list(self.skill_names),
        }


MINIMAL_CONTROL = CampaignVariant(
    name="minimal-control",
    label="Minimal Control",
    description=(
        "同一 AgentLoop、模型、任务、预算和安全边界；暴露完整工具集并关闭 Skill。"
    ),
    tool_routing_mode="all",
    skill_mode="none",
)
GOVERNED_RUNTIME = CampaignVariant(
    name="governed-runtime",
    label="Governed Runtime",
    description=(
        "同一 AgentLoop、模型、任务、预算和安全边界；启用去重后的 task-aware routing "
        "与单一 SWE-bench 修复 Skill。"
    ),
    tool_routing_mode="task-aware",
    skill_mode="auto",
    skill_names=("swebench_repair",),
)
DEFAULT_CAMPAIGN_VARIANTS = (MINIMAL_CONTROL, GOVERNED_RUNTIME)


# 核心数据：一次 repeated matched campaign 的完整、无歧义输入。
@dataclass(frozen=True)
class BenchmarkCampaignRequest:
    """固定 case、重复次数、Runtime preset 和 artifact 策略。

    ``benchmark`` 保存所有不应变化的模型、预算、安全和隔离参数；``case_ids``、
    ``repetitions`` 与 ``variants`` 共同定义执行矩阵；``campaign_id`` 是恢复同一实验
    的稳定主键。``output_root`` 是私有原始证据；``publish_root`` 接收经过现有
    key/path sanitizer 的 manifest、summary、scorecard 和 result 投影，但该过滤器
    不是通用 secret/PII 检测器。
    """

    benchmark: SwebenchRunRequest
    case_ids: tuple[str, ...]
    campaign_id: str
    regression_set: str = "infrastructure-smoke-5"
    repetitions: int = 3
    # Domain 只保存稳定的相对路径契约，不依赖外层 filesystem layout 模块。
    output_root: str = ".agent_forge/runs/campaigns"
    publish_root: str = ""
    resume: bool = True
    rerun_incomplete_slots: bool = True
    allow_dirty: bool = False
    max_infrastructure_attempts: int = 2
    max_parallel_slots: int = 1
    variants: tuple[CampaignVariant, ...] = DEFAULT_CAMPAIGN_VARIANTS
    cohort: CohortSelection | None = None

    def __post_init__(self) -> None:
        if not _CAMPAIGN_ID.fullmatch(self.campaign_id) or self.campaign_id in {
            ".",
            "..",
        }:
            raise ValueError(
                "campaign_id must be 1-80 safe filename characters and cannot be '.' or '..'"
            )
        if not self.case_ids:
            raise ValueError("campaign requires at least one case")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("campaign case_ids must be unique")
        if not 1 <= self.repetitions <= 20:
            raise ValueError("campaign repetitions must be between 1 and 20")
        names = [variant.name for variant in self.variants]
        if not names or len(set(names)) != len(names):
            raise ValueError("campaign requires one or more uniquely named variants")
        if not 1 <= self.max_infrastructure_attempts <= 2:
            raise ValueError("max_infrastructure_attempts must be 1 or 2")
        if not 1 <= self.max_parallel_slots <= 4:
            raise ValueError("max_parallel_slots must be between 1 and 4")
        if not self.rerun_incomplete_slots and self.max_infrastructure_attempts != 1:
            raise ValueError(
                "strict started-slot no-rerun requires max_infrastructure_attempts=1"
            )
        if self.benchmark.cases_file:
            raise ValueError(
                "campaign currently requires a versioned dataset/regression set; "
                "custom cases_file needs a content digest contract first"
            )
        if self.cohort is not None:
            if self.case_ids != self.cohort.case_ids:
                raise ValueError(
                    "campaign case_ids must exactly match the selected cohort shard"
                )
            if self.benchmark.dataset_name != self.cohort.dataset_name:
                raise ValueError("campaign dataset must match the cohort manifest")
            if self.benchmark.dataset_revision != self.cohort.dataset_revision:
                raise ValueError(
                    "campaign dataset revision must match the cohort manifest"
                )
            if self.benchmark.split != self.cohort.split:
                raise ValueError("campaign split must match the cohort manifest")

    def identity(self) -> dict[str, Any]:
        """返回可持久化的实验身份，刻意排除密钥和本机绝对路径。"""

        base = asdict(self.benchmark)
        for key in (
            "api_key",
            "repo_cache",
            "output_root",
            "memory_root",
            "memory_namespace",
            "instance_ids",
            "limit",
            "agent_mode",
            "tool_routing_mode",
            "skill_mode",
            "skill_names",
            "skill_manifest_files",
            "memory_max_chars",
        ):
            base.pop(key, None)
        base["base_url"] = _safe_base_url(str(base.get("base_url") or ""))
        base["cases_file"] = "custom" if base.get("cases_file") else ""
        identity = {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "regression_set": self.regression_set,
            "case_ids": list(self.case_ids),
            "repetitions": self.repetitions,
            "comparison_factor": (
                "runtime-preset" if len(self.variants) > 1 else "none"
            ),
            "max_infrastructure_attempts": self.max_infrastructure_attempts,
            "benchmark": base,
            "variants": [variant.to_dict() for variant in self.variants],
        }
        # 省略兼容默认值，保持既有 campaign 的 configuration digest 可恢复；
        # 严格 Pass@1 才把 no-rerun 策略写入冻结身份。
        if not self.rerun_incomplete_slots:
            identity["rerun_incomplete_slots"] = False
        if self.max_parallel_slots != 1:
            identity["max_parallel_slots"] = self.max_parallel_slots
        if self.cohort is not None:
            identity["cohort"] = self.cohort.to_dict()
        return identity


# 核心数据：campaign 中一个 case、一次 repetition、一个 variant 的执行槽位。
@dataclass
class CampaignRunRecord:
    """campaign 中一个可独立 checkpoint 的执行槽位。

    ``key`` 唯一绑定 case、重复序号和 variant；``status`` 与 ``attempts`` 支撑恢复；
    ``run_dir`` 指向私有原始证据；``evidence`` 只保存聚合所需的类型化事实。
    """

    key: str
    ordinal: int
    case_id: str
    repetition: int
    variant: str
    status: str = "pending"
    attempts: int = 0
    run_id: str = ""
    run_dir: str = ""
    scorecard_sha256: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "ordinal": self.ordinal,
            "case_id": self.case_id,
            "repetition": self.repetition,
            "variant": self.variant,
            "status": self.status,
            "attempts": self.attempts,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "scorecard_sha256": self.scorecard_sha256,
            "evidence": self.evidence,
            "attempt_history": self.attempt_history,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CampaignRunRecord":
        return cls(
            key=str(data.get("key") or ""),
            ordinal=int(data.get("ordinal") or 0),
            case_id=str(data.get("case_id") or ""),
            repetition=int(data.get("repetition") or 0),
            variant=str(data.get("variant") or ""),
            status=str(data.get("status") or "pending"),
            attempts=int(data.get("attempts") or 0),
            run_id=str(data.get("run_id") or ""),
            run_dir=str(data.get("run_dir") or ""),
            scorecard_sha256=str(data.get("scorecard_sha256") or ""),
            evidence=dict(data.get("evidence") or {}),
            attempt_history=[
                dict(item)
                for item in data.get("attempt_history") or []
                if isinstance(item, dict)
            ],
            error=str(data.get("error") or ""),
        )


# 核心数据：可在每个执行槽位后通过同目录 replace 发布、可恢复的 campaign 状态。
@dataclass
class CampaignState:
    """整个 campaign 的 persisted checkpoint；不宣称已执行文件/目录 fsync。

    ``config_digest`` 同时绑定实验配置和 source identity，避免恢复时混入另一版代码；
    ``records`` 是完整 planned denominator，未完成槽位也不会从统计分母中消失。
    """

    campaign_id: str
    config_digest: str
    config: dict[str, Any]
    source: dict[str, Any]
    created_at: str
    updated_at: str
    records: list[CampaignRunRecord]
    status: str = "running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "config_digest": self.config_digest,
            "config": self.config,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "records": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CampaignState":
        if int(data.get("schema_version") or 0) != CAMPAIGN_SCHEMA_VERSION:
            raise ValueError("unsupported benchmark campaign schema")
        return cls(
            campaign_id=str(data.get("campaign_id") or ""),
            config_digest=str(data.get("config_digest") or ""),
            config=dict(data.get("config") or {}),
            source=dict(data.get("source") or {}),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            status=str(data.get("status") or "running"),
            records=[
                CampaignRunRecord.from_dict(item)
                for item in data.get("records") or []
                if isinstance(item, dict)
            ],
        )
# endregion 1. 输入与状态契约结束


# region 2. 预注册槽位：配置/源码 identity → 固定 denominator 与交错 ordinal
def campaign_config_digest(identity: dict[str, Any], source: dict[str, Any]) -> str:
    """把实验配置和 source revision 绑定，防止恢复时混入另一版代码。"""

    payload = {"config": identity, "source": source}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_campaign_records(
    request: BenchmarkCampaignRequest,
) -> list[CampaignRunRecord]:
    """交错 variant 顺序，降低 provider 时间漂移总是偏向同一 variant 的风险。"""

    # 三层循环只展开预注册矩阵，不执行任何 Case。
    records: list[CampaignRunRecord] = []
    ordinal = 0
    variants = list(request.variants)
    for repetition in range(1, request.repetitions + 1):
        for case_index, case_id in enumerate(request.case_ids):
            # 相邻 case/repetition 反转 variant 次序，降低 provider 时间漂移的固定偏向。
            ordered = (
                variants if (repetition + case_index) % 2 else list(reversed(variants))
            )
            for variant in ordered:
                ordinal += 1
                records.append(
                    CampaignRunRecord(
                        key=(
                            f"{safe_id(case_id)}__r{repetition:02d}__{safe_id(variant.name)}"
                        ),
                        ordinal=ordinal,
                        case_id=case_id,
                        repetition=repetition,
                        variant=variant.name,
                    )
                )
    return records
# endregion 2. 预注册槽位结束


# region 3. Variant 汇总：planned denominator → rates/cost → claim boundary
# 主要入口：按 planned denominator 聚合进度、成本和 claim-safe 质量证据。
def summarize_campaign(state: CampaignState) -> dict[str, Any]:
    """聚合重复运行，并把预注册解决率与显式 official verdict 覆盖率分开。

    伪代码：按 variant 累积所有 planned slots → 加入 retry/usage/result facts
    → 用各自明确分母计算 rate → 构造 official-only 与 selected-sample 配对视图。
    """

    # region 3.1 原始计数：failed/pending 仍留在 planned denominator
    variants = {
        str(item.get("name")): _empty_variant_summary(
            str(item.get("label") or item.get("name"))
        )
        for item in state.config.get("variants") or []
        if isinstance(item, dict)
    }
    for record in state.records:
        summary = variants.setdefault(
            record.variant, _empty_variant_summary(record.variant)
        )
        summary["planned"] += 1
        for prior_attempt in record.attempt_history:
            prior_evidence = prior_attempt.get("evidence")
            if not isinstance(prior_evidence, dict):
                continue
            summary["retry_attempts"] += 1
            summary["retry_tokens"] += int(prior_evidence.get("total_tokens") or 0)
            summary["retry_estimated_cost_usd"] += float(
                prior_evidence.get("estimated_cost_usd") or 0.0
            )
        if record.status == "failed":
            summary["failed"] += 1
            continue
        if record.status != "completed":
            continue
        summary["completed"] += 1
        evidence = record.evidence
        summary["patch_generated"] += int(bool(evidence.get("patch_generated")))
        summary["local_verified"] += int(
            bool(evidence.get("patch_generated"))
            and evidence.get("local_validation_status") == "passed"
        )
        failure = str(evidence.get("failure_class") or "unclassified")
        summary["infrastructure_failures"] += int(
            failure in RETRYABLE_INFRASTRUCTURE_FAILURES
        )
        official = str(evidence.get("official_evaluation_status") or "not_evaluated")
        if official in OFFICIAL_DECIDED:
            summary["official_evaluated"] += 1
            summary["official_resolved"] += int(official == "official_resolved")
        for key in (
            "total_tokens",
            "llm_latency_ms",
            "tool_calls",
            "failed_tool_calls",
        ):
            summary[key] += int(evidence.get(key) or 0)
        summary["estimated_cost_usd"] += float(
            evidence.get("estimated_cost_usd") or 0.0
        )
        summary["failure_classes"][failure] = (
            int(summary["failure_classes"].get(failure) or 0) + 1
        )
    # endregion 3.1 原始计数结束

    # region 3.2 派生率：每个指标明确使用 planned、completed 或 official_count
    for summary in variants.values():
        planned = int(summary["planned"])
        official_count = int(summary["official_evaluated"])
        completed = int(summary["completed"])
        summary["completion_rate"] = completed / planned if planned else None
        summary["patch_generated_rate"] = (
            int(summary["patch_generated"]) / planned if planned else None
        )
        # 主指标使用预注册样本作为分母；另一比率只使用 explicit official verdict，
        # 防止把 evaluator 覆盖子集的接受率误读为随机任务解决率。
        summary["official_resolved_rate"] = (
            int(summary["official_resolved"]) / planned if planned else None
        )
        summary["official_resolved_wilson_95_ci"] = _wilson_95_interval(
            int(summary["official_resolved"]),
            planned,
        )
        summary["evaluated_patch_acceptance_rate"] = (
            int(summary["official_resolved"]) / official_count
            if official_count
            else None
        )
        summary["official_evaluation_coverage_rate"] = (
            official_count / planned if planned else None
        )
        tool_calls = int(summary["tool_calls"])
        summary["failed_tool_call_rate"] = (
            int(summary["failed_tool_calls"]) / tool_calls if tool_calls else None
        )
        summary["estimated_cost_usd"] = round(float(summary["estimated_cost_usd"]), 6)
        summary["retry_estimated_cost_usd"] = round(
            float(summary["retry_estimated_cost_usd"]), 6
        )
        summary["execution_estimated_cost_usd"] = round(
            float(summary["estimated_cost_usd"])
            + float(summary["retry_estimated_cost_usd"]),
            6,
        )
        summary["mean_tokens_per_completed_run"] = (
            int(summary["total_tokens"]) / completed if completed else None
        )
        summary["failure_classes"] = dict(sorted(summary["failure_classes"].items()))
    # endregion 3.2 派生率结束

    # region 3.3 配对与最终投影：两种配对分母保持独立命名
    paired = _paired_official_summary(state)
    paired_sample = _paired_sample_summary(state)
    status_counts: dict[str, int] = {}
    for record in state.records:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
    single_runtime = len(variants) == 1
    max_infrastructure_attempts = int(
        state.config.get("max_infrastructure_attempts") or 2
    )
    return {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": state.campaign_id,
        "status": state.status,
        "source": state.source,
        "config_digest": state.config_digest,
        "planned_runs": len(state.records),
        "status_counts": dict(sorted(status_counts.items())),
        "variants": variants,
        "paired_official": paired,
        "paired_sample": paired_sample,
        "execution_estimated_cost_usd": round(
            sum(
                float(item.get("execution_estimated_cost_usd") or 0.0)
                for item in variants.values()
            ),
            6,
        ),
        "claim_boundary": {
            "comparison_factor": (
                "none; single pre-registered runtime snapshot"
                if single_runtime
                else "runtime-preset; multiple control features differ"
            ),
            "patch_rate_denominator": "all planned runs",
            "official_resolved_rate_denominator": "all pre-registered runs",
            "evaluated_patch_acceptance_denominator": "runs with explicit official resolved/unresolved outcome",
            "infrastructure_policy": (
                "no whole-case infrastructure retry; infrastructure slots stay visible"
                if max_infrastructure_attempts == 1
                else "one bounded whole-case infrastructure retry; exhausted infrastructure slots stay visible"
            ),
            "statistical_limit": "pre-registered engineering sample, not an official leaderboard or population estimate",
        },
    }
    # endregion 3.3 配对与最终投影结束
# endregion 3. Variant 汇总结束


def _wilson_95_interval(resolved: int, planned: int) -> list[float] | None:
    """计算二项比例的 Wilson 95% 区间，避免把小样本点估计写成精确真值。"""

    if planned <= 0:
        return None
    z = 1.959963984540054
    proportion = resolved / planned
    z_squared = z * z
    denominator = 1 + z_squared / planned
    centre = proportion + z_squared / (2 * planned)
    margin = z * (
        (proportion * (1 - proportion) / planned + z_squared / (4 * planned**2)) ** 0.5
    )
    return [
        round(max(0.0, (centre - margin) / denominator), 6),
        round(min(1.0, (centre + margin) / denominator), 6),
    ]


def _empty_variant_summary(label: str) -> dict[str, Any]:
    return {
        "label": label,
        "planned": 0,
        "completed": 0,
        "failed": 0,
        "patch_generated": 0,
        "local_verified": 0,
        "official_evaluated": 0,
        "official_resolved": 0,
        "infrastructure_failures": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "retry_attempts": 0,
        "retry_tokens": 0,
        "retry_estimated_cost_usd": 0.0,
        "llm_latency_ms": 0,
        "tool_calls": 0,
        "failed_tool_calls": 0,
        "failure_classes": {},
    }


# region 4. 配对视图：official-only 与 selected-sample 不共享 denominator
def _paired_official_summary(state: CampaignState) -> dict[str, Any]:
    """只比较两侧都有 explicit official resolved/unresolved 的同 Case 同 repetition。"""

    variant_names = [
        str(item.get("name"))
        for item in state.config.get("variants") or []
        if isinstance(item, dict)
    ]
    if len(variant_names) != 2:
        return {"evaluated_pairs": 0, "wins": {}, "ties": 0}
    control, treatment = variant_names
    grouped: dict[tuple[str, int], dict[str, CampaignRunRecord]] = {}
    for record in state.records:
        grouped.setdefault((record.case_id, record.repetition), {})[record.variant] = (
            record
        )
    control_wins = 0
    treatment_wins = 0
    ties = 0
    evaluated_pairs = 0
    # 缺一侧、未完成或任一侧没有 explicit official verdict 的 pair 不进入此分母。
    for pair in grouped.values():
        left = pair.get(control)
        right = pair.get(treatment)
        if (
            not left
            or not right
            or left.status != "completed"
            or right.status != "completed"
        ):
            continue
        left_status = str(
            left.evidence.get("official_evaluation_status") or "not_evaluated"
        )
        right_status = str(
            right.evidence.get("official_evaluation_status") or "not_evaluated"
        )
        if left_status not in OFFICIAL_DECIDED or right_status not in OFFICIAL_DECIDED:
            continue
        evaluated_pairs += 1
        left_resolved = left_status == "official_resolved"
        right_resolved = right_status == "official_resolved"
        if left_resolved and not right_resolved:
            control_wins += 1
        elif right_resolved and not left_resolved:
            treatment_wins += 1
        else:
            ties += 1
    return {
        "control": control,
        "treatment": treatment,
        "evaluated_pairs": evaluated_pairs,
        "wins": {control: control_wins, treatment: treatment_wins},
        "ties": ties,
    }


def _paired_sample_summary(state: CampaignState) -> dict[str, Any]:
    """比较两侧均完成且非 infrastructure failure 的预注册样本。

    当前投影只有 ``official_resolved`` 计成功；稳定 no-patch、未 resolved 或没有
    resolved verdict 的完成槽位均按未解决进入 selected-sample 视图。它不是
    official-only 配对率。
    """

    variant_names = [
        str(item.get("name"))
        for item in state.config.get("variants") or []
        if isinstance(item, dict)
    ]
    if len(variant_names) != 2:
        return {
            "adjudicated_pairs": 0,
            "excluded_infrastructure_pairs": 0,
            "wins": {},
            "ties": 0,
        }
    control, treatment = variant_names
    grouped: dict[tuple[str, int], dict[str, CampaignRunRecord]] = {}
    for record in state.records:
        grouped.setdefault((record.case_id, record.repetition), {})[record.variant] = (
            record
        )

    wins = {control: 0, treatment: 0}
    ties = 0
    adjudicated_pairs = 0
    excluded_infrastructure_pairs = 0
    # 缺失/未完成或任一侧为 infrastructure failure 的 pair 单独计入 excluded。
    for pair in grouped.values():
        left = pair.get(control)
        right = pair.get(treatment)
        if (
            not left
            or not right
            or left.status != "completed"
            or right.status != "completed"
        ):
            excluded_infrastructure_pairs += 1
            continue
        failure_classes = {
            str(left.evidence.get("failure_class") or ""),
            str(right.evidence.get("failure_class") or ""),
        }
        if failure_classes & RETRYABLE_INFRASTRUCTURE_FAILURES:
            excluded_infrastructure_pairs += 1
            continue
        adjudicated_pairs += 1
        left_resolved = (
            str(left.evidence.get("official_evaluation_status") or "")
            == "official_resolved"
        )
        right_resolved = (
            str(right.evidence.get("official_evaluation_status") or "")
            == "official_resolved"
        )
        if left_resolved and not right_resolved:
            wins[control] += 1
        elif right_resolved and not left_resolved:
            wins[treatment] += 1
        else:
            ties += 1
    return {
        "control": control,
        "treatment": treatment,
        "adjudicated_pairs": adjudicated_pairs,
        "excluded_infrastructure_pairs": excluded_infrastructure_pairs,
        "wins": wins,
        "ties": ties,
    }
# endregion 4. 配对视图结束


def _safe_base_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
