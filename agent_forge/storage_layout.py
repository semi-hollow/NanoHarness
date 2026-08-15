"""NanoHarness 本地运行目录的唯一布局契约。"""

from __future__ import annotations

from pathlib import Path


AGENT_FORGE_ROOT = Path(".agent_forge")
RUNS_ROOT = AGENT_FORGE_ROOT / "runs"
INTERNAL_ROOT = AGENT_FORGE_ROOT / "internal"
ARCHIVE_ROOT = AGENT_FORGE_ROOT / "archive"

STATE_ROOT = INTERNAL_ROOT / "state"
APPROVAL_ROOT = STATE_ROOT / "approvals"
HUMAN_INPUT_ROOT = STATE_ROOT / "human_input"
OPERATION_LEDGER_ROOT = STATE_ROOT / "operation_ledger"
# Long-Term Memory 跨 Run、跨 Project；物理根属于当前机器，不属于项目控制状态。
MEMORY_ROOT = Path.home() / ".agent_forge" / "memory"
SESSIONS_ROOT = STATE_ROOT / "sessions"

DEBUG_LAB_ROOT = INTERNAL_ROOT / "debug-lab"
DEBUG_LAB_STATE_ROOT = DEBUG_LAB_ROOT / "state"

CACHE_ROOT = INTERNAL_ROOT / "cache"
BENCH_CACHE_ROOT = CACHE_ROOT / "bench"
BENCH_REPO_CACHE_ROOT = BENCH_CACHE_ROOT / "repos"
WORKTREE_ROOT = CACHE_ROOT / "worktrees"
SNAPSHOT_ROOT = CACHE_ROOT / "snapshots"

INDEX_ROOT = INTERNAL_ROOT / "index"
GENERATED_ROOT = INTERNAL_ROOT / "generated"
CASE_INSPECTIONS_ROOT = GENERATED_ROOT / "case-inspections"
EVALUATION_DATA_ROOT = GENERATED_ROOT / "evaluation"

CAMPAIGN_RUN_ROOT = RUNS_ROOT / "campaigns"
SHOWCASE_RUN_ROOT = RUNS_ROOT / "showcases"
BENCHMARK_RUN_ROOT = RUNS_ROOT / "benchmarks"
EXPERIMENT_RUN_ROOT = RUNS_ROOT / "experiments"

CONTROL_STATE_ROOTS = {
    "approvals": APPROVAL_ROOT,
    "human_input": HUMAN_INPUT_ROOT,
    "operation_ledger": OPERATION_LEDGER_ROOT,
}

LEGACY_CONTROL_ROOTS = {
    AGENT_FORGE_ROOT / "approvals": APPROVAL_ROOT,
    AGENT_FORGE_ROOT / "human_input": HUMAN_INPUT_ROOT,
    AGENT_FORGE_ROOT / "operation_ledger": OPERATION_LEDGER_ROOT,
}


def control_state_root(name: str) -> Path:
    """返回控制面状态目录；未知名称直接拒绝，避免再次平铺新目录。"""

    try:
        return CONTROL_STATE_ROOTS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported control state root: {name}") from exc


def ensure_storage_layout(workspace: Path) -> None:
    """预建稳定目录骨架，让运行证据与控制面 JSON 可直接定位。"""

    for relative in (
        RUNS_ROOT,
        BENCHMARK_RUN_ROOT,
        EXPERIMENT_RUN_ROOT,
        CAMPAIGN_RUN_ROOT,
        SHOWCASE_RUN_ROOT,
        ARCHIVE_ROOT,
        INDEX_ROOT,
        CACHE_ROOT,
        GENERATED_ROOT,
        APPROVAL_ROOT,
        HUMAN_INPUT_ROOT,
        OPERATION_LEDGER_ROOT,
        SESSIONS_ROOT,
        DEBUG_LAB_ROOT,
        DEBUG_LAB_STATE_ROOT,
    ):
        (workspace / relative).mkdir(parents=True, exist_ok=True)


__all__ = [
    "AGENT_FORGE_ROOT",
    "APPROVAL_ROOT",
    "ARCHIVE_ROOT",
    "BENCHMARK_RUN_ROOT",
    "BENCH_CACHE_ROOT",
    "BENCH_REPO_CACHE_ROOT",
    "CACHE_ROOT",
    "CAMPAIGN_RUN_ROOT",
    "CASE_INSPECTIONS_ROOT",
    "CONTROL_STATE_ROOTS",
    "DEBUG_LAB_STATE_ROOT",
    "DEBUG_LAB_ROOT",
    "EVALUATION_DATA_ROOT",
    "EXPERIMENT_RUN_ROOT",
    "GENERATED_ROOT",
    "HUMAN_INPUT_ROOT",
    "INDEX_ROOT",
    "INTERNAL_ROOT",
    "LEGACY_CONTROL_ROOTS",
    "MEMORY_ROOT",
    "OPERATION_LEDGER_ROOT",
    "RUNS_ROOT",
    "SESSIONS_ROOT",
    "SHOWCASE_RUN_ROOT",
    "SNAPSHOT_ROOT",
    "STATE_ROOT",
    "WORKTREE_ROOT",
    "control_state_root",
    "ensure_storage_layout",
]
