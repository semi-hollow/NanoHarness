from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SwebenchRunRequest:

    dataset_name: str = "princeton-nlp/SWE-bench_Lite"
    split: str = "test"
    limit: int = 1
    instance_ids: tuple[str, ...] = ()
    cases_file: str | None = None
    provider: str = "deepseek"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    max_steps: int = 16
    max_context_chars: int = 12000
    max_prompt_tokens: int = 32_768
    reserved_output_tokens: int = 4_096
    max_tool_calls_per_turn: int = 4
    cost_budget_usd: float | None = None
    timeout_seconds: float = 900.0
    repo_cache: str = ".agent_forge/bench/repos"
    output_root: str = ".agent_forge/runs"
    direct_baseline: bool = False
    evaluate: bool = False
    max_workers: int = 1
    namespace_empty: bool = False
    agent_mode: str = "single"
    profile: str = "coding_fix"
    max_revision_rounds: int = 2
    tool_routing_mode: str = "task-aware"
    skill_mode: str = "auto"
    skill_names: tuple[str, ...] = ()
    skill_manifest_files: tuple[str, ...] = ()
    memory_root: str = ""
    memory_namespace: str = ""
    memory_recall_limit: int = 0
    execution_mode: str = "local"
    network_policy: str = "deny"
    keep_worktree: bool = False
    container_runtime: str = "docker"
    container_image: str = "python:3.11-slim"
    container_cpus: float = 1.0
    container_memory: str = "1g"
    container_pids_limit: int = 256
    container_read_only: bool = True


@dataclass(frozen=True)
class BenchRunLayout:

    output_dir: Path
    predictions_path: Path
    baseline_predictions_path: Path | None

    def case_dir(self, instance_id: str) -> Path:
        return self.output_dir / "cases" / safe_id(instance_id)


def safe_id(value: str) -> str:

    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
