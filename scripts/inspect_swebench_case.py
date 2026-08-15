#!/usr/bin/env python3
"""按 SWE-bench 原字段查看一个 ``instance_id``，默认不做语义加工。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from agent_forge.bench.api import inspect_swebench_case
from agent_forge.bench.domain.config import safe_id
from agent_forge.bench.presentation.case_inspection import render_source_case_inspection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
DATASET_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / ".agent_forge" / "internal" / "generated" / "case-inspections"
)
LOCAL_POST_RUN_CASE_FILES = (
    PROJECT_ROOT
    / ".agent_forge"
    / "tool-aci-golden-20-r2"
    / "dataset"
    / "official-cases.sealed.json",
    PROJECT_ROOT
    / ".agent_forge"
    / "tool-aci-golden-20-v1"
    / "dataset"
    / "official-cases.sealed.json",
)
EXPERIMENT_RESULTS = {
    "r1": PROJECT_ROOT / "benchmarks" / "experiments" / "tool-aci-r1" / "result.json",
    "r2": PROJECT_ROOT / "benchmarks" / "experiments" / "tool-aci-r2" / "result.json",
}


def build_parser() -> argparse.ArgumentParser:
    """定义只读入口；``instance_id`` 也可来自 Run Configuration 环境变量。"""

    parser = argparse.ArgumentParser(
        description=(
            "Print the source fields of one pinned SWE-bench Verified case. "
            "The default output adds no project summary, experiment result, "
            "gold patch, or test patch."
        )
    )
    parser.add_argument(
        "instance_id",
        nargs="?",
        default="",
        help="SWE-bench instance_id, for example astropy__astropy-14182.",
    )
    parser.add_argument(
        "--experiment",
        choices=["none", "r1", "r2", "both"],
        default=os.getenv("SWE_BENCH_EXPERIMENT", "none"),
        help="Optionally append post-run R1/R2 evidence; default: none.",
    )
    parser.add_argument(
        "--cases-file",
        help="Optional local JSON/JSONL dataset projection; otherwise use the pinned dataset.",
    )
    parser.add_argument(
        "--output",
        help=(
            "Output path; defaults to "
            ".agent_forge/internal/generated/case-inspections/<instance_id>.md."
        ),
    )
    return parser


def render_experiment_observations(instance_id: str, selection: str) -> str:
    """只投影已发布的 per-Case outcome 迁移，不读取 Trace、Gold 或测试日志。"""

    experiment_ids = {
        "none": (),
        "r1": ("r1",),
        "r2": ("r2",),
        "both": ("r1", "r2"),
    }[selection]
    rows: list[str] = []
    sources: list[str] = []
    for experiment_id in experiment_ids:
        result_path = EXPERIMENT_RESULTS[experiment_id]
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        transition = next(
            (
                item
                for item in payload["paired"]["transitions"]
                if item["instance_id"] == instance_id
            ),
            None,
        )
        if transition is None:
            continue
        treatment_id = experiment_id
        rows.append(
            f"| {experiment_id.upper()} | {transition['r0']} | "
            f"{transition[treatment_id]} | {transition['transition']} | "
            f"{transition['subset']} |"
        )
        sources.append(result_path.relative_to(PROJECT_ROOT).as_posix())
    if not rows:
        return ""
    return "\n".join(
        [
            "",
            "## Optional post-run Tool / ACI observations",
            "",
            "> 这一节来自实验完成后的机器汇总，不属于 Agent 当时收到的任务输入。",
            "",
            "| 实验 | R0 | Treatment | 迁移 | 子集 |",
            "| --- | --- | --- | --- | --- |",
            *rows,
            "",
            "证据文件：" + "、".join(f"`{source}`" for source in sources) + "。",
        ]
    )


def local_post_run_cases_file(instance_id: str) -> Path | None:
    """优先复用已完成实验的本地数据；只用它渲染，绝不送回 Agent。"""

    for path in LOCAL_POST_RUN_CASE_FILES:
        if not path.is_file():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("instance_id") == instance_id
            for row in rows
        ):
            return path
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """打印并保存数据集原字段，方便 Run Configuration 直接查看。"""

    args = build_parser().parse_args(argv)
    instance_id = (args.instance_id or os.getenv("SWE_BENCH_CASE_ID", "")).strip()
    if not instance_id:
        raise ValueError(
            "instance_id is required; set SWE_BENCH_CASE_ID in the Run Configuration"
        )
    cases_file = args.cases_file or local_post_run_cases_file(instance_id)
    inspection = inspect_swebench_case(
        instance_id,
        dataset_name=DATASET_NAME,
        dataset_revision=DATASET_REVISION,
        split="test",
        cases_file=str(cases_file) if cases_file else None,
    )
    document = render_source_case_inspection(
        inspection,
        dataset_name=DATASET_NAME,
        dataset_revision=DATASET_REVISION,
        split="test",
    ).rstrip()
    observations = render_experiment_observations(instance_id, args.experiment)
    if observations:
        document += "\n" + observations
    document += "\n"

    output_path = (
        Path(args.output)
        if args.output
        else DEFAULT_OUTPUT_ROOT / f"{safe_id(instance_id)}.md"
    )
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    print(document, end="")
    print(f"\nSAVED: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
