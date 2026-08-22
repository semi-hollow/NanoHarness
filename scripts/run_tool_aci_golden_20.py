#!/usr/bin/env python3
"""Tool / ACI Golden-20 的配置驱动最小评测流水线。

默认只打印计划；``--execute`` 才调用模型；``--import-history`` 只为已有
``forge bench swebench`` 产物生成统一索引。结果归因和文字报告不属于本入口。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agent_forge.bench.domain.evaluation_contract import EvaluationContract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "benchmarks/experiments/tool-aci-runner-v1.json"
SHARDS = ("shard-a", "shard-b", "shard-c", "shard-d")
# Canonical R1/R2 indexes identify the launcher revision that created those frozen
# derived artifacts. Structural source moves must not rewrite historical provenance.
HISTORICAL_IMPORT_LAUNCHER_SHA256 = (
    "a53348a54417792f18408779793a2b4a81a1d4f3869737fc157a86583912bae1"
)


class PipelineRefused(ValueError):
    """配置、源码或结构化产物不满足运行合同。"""


@dataclass(frozen=True)
class Plan:
    """实验合同与本地 I/O 坐标的组合结果。"""

    experiment: str
    variant: str
    source_commit: str
    contract: EvaluationContract
    config_path: Path
    protocol_path: Path
    agent_dataset: Path
    official_dataset: Path
    historical_runs_root: Path
    manifest_output: Path

    @property
    def experiment_id(self) -> str:
        return self.contract.experiment_id

    @property
    def case_ids(self) -> tuple[str, ...]:
        return self.contract.case_ids

    @property
    def shard_size(self) -> int:
        return self.contract.shard_size

    @property
    def benchmark_args(self) -> tuple[str, ...]:
        return self.contract.benchmark_args

    @property
    def shards(self) -> tuple[tuple[str, ...], ...]:
        return self.contract.shards


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--experiment", default="r2")
    parser.add_argument("--variant", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--import-history", action="store_true")
    parser.add_argument("--source-root", default=str(PROJECT_ROOT))
    parser.add_argument("--output-root", default="")
    parser.add_argument("--manifest-output", default="")
    parser.add_argument(
        "--repo-cache",
        default=".agent_forge/internal/cache/bench/repos",
    )
    return parser


def load_plan(
    config_path: str | Path,
    experiment: str,
    variant: str = "",
    *,
    project_root: Path = PROJECT_ROOT,
) -> Plan:
    root = project_root.resolve()
    config_file = _resolve(root, config_path)
    config = _read_json(config_file, "pipeline config")
    contract_raw = config.get("evaluation_contract")
    if not isinstance(contract_raw, dict):
        raise PipelineRefused("evaluation contract is missing")
    experiments = config.get("experiments")
    raw = experiments.get(experiment) if isinstance(experiments, dict) else None
    if config.get("schema_version") != 1 or not isinstance(raw, dict):
        raise PipelineRefused(f"unknown experiment or schema: {experiment}")
    selected = variant or _text(raw, "default_variant")
    variants = raw.get("variants")
    variant_raw = variants.get(selected) if isinstance(variants, dict) else None
    if not isinstance(variant_raw, dict):
        raise PipelineRefused(f"unknown variant: {selected}")

    protocol_path = _resolve(root, _text(raw, "protocol_path"))
    protocol = _read_json(protocol_path, "experiment protocol")
    case_ids = tuple(str(item) for item in protocol.get("case_ids", []))
    if not isinstance(variants, dict) or any(
        not isinstance(name, str) or not isinstance(value, dict)
        for name, value in variants.items()
    ):
        raise PipelineRefused("variants must be a JSON object")
    try:
        contract = EvaluationContract(
            experiment_id=_text(raw, "experiment_id"),
            comparison=str(contract_raw.get("comparison") or ""),
            primary_metric=str(contract_raw.get("primary_metric") or ""),
            case_ids=case_ids,
            ordered_case_ids_sha256=str(protocol.get("ordered_case_ids_sha256") or ""),
            shard_size=int(raw.get("shard_size") or 0),
            benchmark_args=tuple(str(item) for item in raw.get("benchmark_args", [])),
            variant_sources=tuple(
                (name, _text(value, "source_commit"))
                for name, value in variants.items()
            ),
            correctness_reruns=int(contract_raw.get("correctness_reruns") or 0),
            terminal_outcomes=tuple(
                str(item)
                for item in contract_raw.get(
                    "terminal_outcomes_stay_in_denominator", []
                )
            ),
            analysis_in_pipeline=bool(contract_raw.get("analysis_in_pipeline")),
        )
    except ValueError as exc:
        raise PipelineRefused(f"evaluation contract invalid: {exc}") from exc
    dataset_root = _resolve(root, _text(variant_raw, "dataset_root"))
    plan = Plan(
        experiment=experiment,
        variant=selected,
        source_commit=contract.source_for(selected),
        contract=contract,
        config_path=config_file,
        protocol_path=protocol_path,
        agent_dataset=dataset_root / "agent-cases.json",
        official_dataset=dataset_root / "official-cases.sealed.json",
        historical_runs_root=_resolve(root, _text(variant_raw, "historical_runs_root")),
        manifest_output=_resolve(root, _text(variant_raw, "manifest_output")),
    )
    if len(plan.shards) != len(SHARDS):
        raise PipelineRefused("this config must produce four shards")
    return plan


def build_shard_command(
    plan: Plan,
    shard_index: int,
    *,
    output_root: Path,
    repo_cache: Path,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "apps.cli.dispatch",
        "bench",
        "swebench",
        "--dataset",
        str(plan.official_dataset),
        "--cases-file",
        str(plan.agent_dataset),
        "--limit",
        str(plan.shard_size),
    ]
    for case_id in plan.shards[shard_index]:
        command.extend(("--instance-id", case_id))
    return [
        *command,
        *plan.benchmark_args,
        "--repo-cache",
        str(repo_cache),
        "--output-root",
        str(output_root / SHARDS[shard_index]),
    ]


def render_plan(plan: Plan, *, source_root: Path, output_root: Path) -> str:
    payload = {
        "schema_version": 1,
        "mode": "validate_only",
        "paid_model_calls_started": False,
        "experiment_id": plan.experiment_id,
        "variant": plan.variant,
        "source_commit": plan.source_commit,
        "source_root": _portable(source_root),
        "protocol": _portable(plan.protocol_path),
        "protocol_sha256": _sha256(plan.protocol_path),
        "pipeline_config": _portable(plan.config_path),
        "pipeline_config_sha256": _sha256(plan.config_path),
        "case_count": len(plan.case_ids),
        "shards": [list(items) for items in plan.shards],
        "benchmark_args": list(plan.benchmark_args),
        "output_root": _portable(output_root),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def execute(
    plan: Plan, *, source_root: Path, output_root: Path, repo_cache: Path
) -> Path:
    if _git(source_root, "rev-parse", "HEAD") != plan.source_commit:
        raise PipelineRefused("source checkout does not match the configured commit")
    if _git(source_root, "status", "--porcelain", "--untracked-files=all"):
        raise PipelineRefused("execute requires a clean source checkout")
    _validate_inputs(plan)
    if not os.getenv("OPENCODE_GO_API_KEY", "").strip():
        raise PipelineRefused("OPENCODE_GO_API_KEY is missing")
    if output_root.exists() and any(output_root.iterdir()):
        raise PipelineRefused(f"output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(source_root), env.get("PYTHONPATH", "")) if item
    )
    for index, shard in enumerate(SHARDS):
        result = subprocess.run(
            build_shard_command(
                plan, index, output_root=output_root, repo_cache=repo_cache
            ),
            cwd=source_root,
            env=env,
            check=False,
        )
        if result.returncode:
            raise PipelineRefused(f"{shard} failed with exit {result.returncode}")
    path = output_root / "execution-manifest.json"
    _write_json(path, index_runs(plan, output_root, "pipeline_execute"))
    return path


def import_history(plan: Plan, output: Path | None = None) -> Path:
    path = output or plan.manifest_output
    _write_json(
        path,
        index_runs(plan, plan.historical_runs_root, "imported_historical_run"),
    )
    return path


def index_runs(plan: Plan, root: Path, origin: str) -> dict[str, Any]:
    indexed = []
    for shard, expected_ids in zip(SHARDS, plan.shards, strict=True):
        parent = root / shard
        run_dirs = sorted(item for item in parent.iterdir() if item.is_dir())
        if len(run_dirs) != 1:
            raise PipelineRefused(f"expected one Run under {parent}")
        run_dir = run_dirs[0]
        scorecard = _read_json(run_dir / "scorecard.json", "scorecard")
        observed = tuple(
            str(item.get("instance_id") or "")
            for item in scorecard.get("cases", [])
            if isinstance(item, dict)
        )
        if observed != expected_ids:
            raise PipelineRefused(f"Case order drift in {run_dir}")
        files = [
            run_dir / name
            for name in ("results.json", "scorecard.json", "predictions.jsonl")
        ]
        aggregates = [
            path
            for path in run_dir.glob("*.json")
            if path.name not in {"results.json", "scorecard.json"}
            and _looks_like_official_aggregate(path)
        ]
        if any(not path.is_file() for path in files) or len(aggregates) != 1:
            raise PipelineRefused(f"incomplete structured artifacts: {run_dir}")
        if _official_case_ids(aggregates[0]) != set(expected_ids):
            raise PipelineRefused(f"official planned denominator drift: {run_dir}")
        indexed.append(
            {
                "shard": shard,
                "case_ids": list(expected_ids),
                "run_id": str(scorecard.get("metadata", {}).get("run_id") or ""),
                "run_dir": _portable(run_dir),
                "artifacts": [
                    _artifact(path, role)
                    for path, role in zip(
                        [*files, aggregates[0]],
                        ["results", "scorecard", "predictions", "official_aggregate"],
                        strict=True,
                    )
                ],
            }
        )
    return {
        "schema_version": 1,
        "artifact_type": "nanoharness_evaluation_execution",
        "experiment_id": plan.experiment_id,
        "variant": plan.variant,
        "source_commit": plan.source_commit,
        "case_count": len(plan.case_ids),
        "case_ids": list(plan.case_ids),
        "pipeline_config": _artifact(plan.config_path, "pipeline_config"),
        "protocol": _artifact(plan.protocol_path, "experiment_config"),
        "provenance": {
            "origin": origin,
            "launcher": "scripts/run_tool_aci_golden_20.py",
            "launcher_sha256": (
                HISTORICAL_IMPORT_LAUNCHER_SHA256
                if origin == "imported_historical_run"
                else _sha256(Path(__file__))
            ),
            "run_artifacts_producer": "forge bench swebench",
            "analysis_included": False,
            "historical_run_predates_this_launcher": origin
            == "imported_historical_run",
        },
        "shards": indexed,
    }


def _validate_inputs(plan: Plan) -> None:
    for path in (plan.agent_dataset, plan.official_dataset):
        if not path.is_file():
            raise PipelineRefused(f"missing dataset input: {path}")
    rows = json.loads(plan.agent_dataset.read_text(encoding="utf-8"))
    if tuple(str(row.get("instance_id") or "") for row in rows) != plan.case_ids:
        raise PipelineRefused("Agent dataset Case order differs from protocol")


def _looks_like_official_aggregate(path: Path) -> bool:
    value = _read_json(path, "official aggregate candidate")
    return value.get("schema_version") == 2 and "resolved_ids" in value


def _official_case_ids(path: Path) -> set[str]:
    value = _read_json(path, "official aggregate")
    buckets = (
        "resolved_ids",
        "unresolved_ids",
        "empty_patch_ids",
        "error_ids",
        "incomplete_ids",
    )
    items = [str(item) for key in buckets for item in value.get(key, [])]
    if len(items) != len(set(items)):
        raise PipelineRefused(f"official outcomes overlap: {path}")
    return set(items)


def _artifact(path: Path, role: str) -> dict[str, str]:
    return {"role": role, "path": _portable(path), "sha256": _sha256(path)}


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineRefused(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineRefused(f"{label} must be a JSON object")
    return value


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise PipelineRefused(f"cannot inspect Git source: {result.stderr.strip()}")
    return result.stdout.strip()


def _text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise PipelineRefused(f"missing string field: {key}")
    return item


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = load_plan(args.config, args.experiment, args.variant)
    source_root = Path(args.source_root).resolve()
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else PROJECT_ROOT
        / ".agent_forge/runs/experiments/tool-aci"
        / plan.experiment
        / plan.variant
    )
    print(render_plan(plan, source_root=source_root, output_root=output_root))
    if not args.execute and not args.import_history:
        print("VALIDATED_ONLY: no provider request was sent and no file was written.")
        return 0
    if args.import_history:
        target = Path(args.manifest_output).resolve() if args.manifest_output else None
        print(f"IMPORTED: {import_history(plan, target)}")
        return 0
    print(
        "COMPLETED: "
        + str(
            execute(
                plan,
                source_root=source_root,
                output_root=output_root,
                repo_cache=_resolve(PROJECT_ROOT, args.repo_cache),
            )
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineRefused as exc:
        raise SystemExit(f"REFUSED: {exc}") from exc
