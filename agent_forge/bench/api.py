"""Benchmark capability 的稳定公共入口。

主能力分成两条链：``run_swebench`` 执行实验并产出证据；
``inspect_swebench_case`` 与两个 profile 查询只读取评测契约，不运行 Agent。
外围 CLI/UI 不应直接导入 dataset adapter 或 application 内部实现。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from agent_forge.bench.adapters.artifact_files import FileBenchArtifacts
from agent_forge.bench.adapters.dataset import SwebenchCaseSource
from agent_forge.bench.application.case_inspection import InspectBenchCase
from agent_forge.bench.application.campaign import BenchmarkCampaignResult
from agent_forge.bench.domain.campaign import BenchmarkCampaignRequest
from agent_forge.bench.domain.case_inspection import (
    BenchmarkCaseInspection,
    BenchmarkCaseProfile,
    BenchmarkSetProfile,
)
from agent_forge.bench.domain.catalog import (
    CASE_PROFILES,
    DEFAULT_DATASET,
    REGRESSION_SETS,
    REGRESSION_SET_PROFILES,
)
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary
from agent_forge.bench.wiring import (
    build_benchmark_campaign_runner,
    build_swebench_runner,
)

# 主要入口：构造并执行一次完整的 SWE-bench 证据运行。
def run_swebench(request: SwebenchRunRequest) -> BenchRunSummary:
    """执行类型化评测请求，并返回可追溯的运行摘要。"""
    run_id = f"swebench-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    artifacts = FileBenchArtifacts()
    layout = artifacts.create_layout(
        request.output_root,
        run_id,
        include_baseline=request.direct_baseline,
    )
    return build_swebench_runner(
        request,
        layout,
        artifacts=artifacts,
    ).execute(request, run_id=run_id, layout=layout)


# 主要入口：执行或恢复一组固定 case、固定身份的重复 Runtime preset 比较。
def run_benchmark_campaign(
    request: BenchmarkCampaignRequest,
    *,
    project_dir: str | Path = ".",
) -> BenchmarkCampaignResult:
    """每个槽位调用正式 ``run_swebench``，并在槽位边界持久化 checkpoint。"""

    root = Path(project_dir).resolve()
    return build_benchmark_campaign_runner(root, run_swebench).execute(request)


def create_campaign_id(prefix: str = "smoke-5") -> str:
    """生成可读且不会碰撞的 campaign id。"""

    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"


# 主要入口：读取一个 benchmark case，但不执行 Agent 或评测。
def inspect_swebench_case(
    instance_id: str,
    *,
    dataset_name: str = DEFAULT_DATASET,
    split: str = "test",
    cases_file: str | None = None,
) -> BenchmarkCaseInspection:
    """返回问题输入、测试契约和默认隐藏的复盘材料。"""

    cases = SwebenchCaseSource().load(
        SwebenchRunRequest(
            dataset_name=dataset_name,
            split=split,
            limit=1,
            instance_ids=(instance_id,),
            cases_file=cases_file,
        )
    )
    return InspectBenchCase.execute(
        cases[0],
        profile=CASE_PROFILES.get(instance_id),
    )


# 主要入口：读取固定集合中每道题的选择理由和 Harness 观察点。
def list_regression_case_profiles(
    regression_set: str = "smoke-5",
) -> tuple[BenchmarkCaseProfile, ...]:
    """返回固定回归集合的人类可读目录，不访问数据集或模型。"""

    try:
        instance_ids = REGRESSION_SETS[regression_set]
    except KeyError as exc:
        raise ValueError(f"Unknown regression set: {regression_set}") from exc
    return tuple(CASE_PROFILES[instance_id] for instance_id in instance_ids)


# 主要入口：读取固定回归集合的选择依据和结论边界。
def get_regression_set_profile(
    regression_set: str = "smoke-5",
) -> BenchmarkSetProfile:
    """返回集合级评测契约，不访问数据集或模型。"""

    try:
        return REGRESSION_SET_PROFILES[regression_set]
    except KeyError as exc:
        raise ValueError(f"Unknown regression set: {regression_set}") from exc


__all__ = [
    "BenchmarkCampaignRequest",
    "BenchmarkCampaignResult",
    "DEFAULT_DATASET",
    "REGRESSION_SETS",
    "SwebenchRunRequest",
    "get_regression_set_profile",
    "inspect_swebench_case",
    "list_regression_case_profiles",
    "run_swebench",
    "run_benchmark_campaign",
    "create_campaign_id",
]
