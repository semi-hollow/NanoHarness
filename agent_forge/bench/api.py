from __future__ import annotations

import time
import uuid

from agent_forge.bench.adapters.artifact_files import FileBenchArtifacts
from agent_forge.bench.domain.catalog import DEFAULT_DATASET, REGRESSION_SETS
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary
from agent_forge.bench.wiring import build_swebench_runner

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

__all__ = ["DEFAULT_DATASET", "REGRESSION_SETS", "SwebenchRunRequest", "run_swebench"]
