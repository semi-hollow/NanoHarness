"""SWE-bench 执行请求与 artifact 布局。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# 核心数据：一次可复现实验的 dataset、模型、Runtime、Memory 与隔离参数。
@dataclass(frozen=True)
class SwebenchRunRequest:
    """``RunSwebench`` 的完整类型化输入。

    字段按数据选择、模型身份、Runtime 预算、输出/evaluation、多 Agent、
    Skill/Memory 和执行环境分组。会影响结果归因的字段必须同步写入
    ``BenchRunSummary`` 与 scorecard metadata。
    """

    # 数据集与 case 选择。
    dataset_name: str = "princeton-nlp/SWE-bench_Lite"
    split: str = "test"
    limit: int = 1
    instance_ids: tuple[str, ...] = ()
    cases_file: str | None = None

    # 实际发送给 provider 的模型身份和 sampling 配置。
    provider: str = "deepseek"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0

    # 每个 AgentLoop case 的资源预算。
    max_steps: int = 16
    max_context_chars: int = 12000
    max_prompt_tokens: int = 32_768
    reserved_output_tokens: int = 4_096
    max_tool_calls_per_turn: int = 4
    cost_budget_usd: float | None = None
    timeout_seconds: float = 900.0

    # Repository cache、artifact 输出与 official evaluation 开关。
    repo_cache: str = ".agent_forge/bench/repos"
    output_root: str = ".agent_forge/runs"
    direct_baseline: bool = False
    evaluate: bool = False
    max_workers: int = 1
    namespace_empty: bool = False

    # single、sequential multi、compare 的 workflow 配置。
    agent_mode: str = "single"
    profile: str = "coding_fix"
    max_revision_rounds: int = 2
    tool_routing_mode: str = "task-aware"

    # Skill 和 evidence-backed long-term memory 的冻结实验输入。
    skill_mode: str = "auto"
    skill_names: tuple[str, ...] = ()
    skill_manifest_files: tuple[str, ...] = ()
    memory_root: str = ""
    memory_namespace: str = ""
    memory_recall_limit: int = 0

    # 代码实际执行位置和 OCI 资源隔离策略。
    execution_mode: str = "local"
    network_policy: str = "deny"
    keep_worktree: bool = False
    container_runtime: str = "docker"
    container_image: str = "python:3.11-slim"
    container_cpus: float = 1.0
    container_memory: str = "1g"
    container_pids_limit: int = 256
    container_read_only: bool = True


# 核心数据：一次 benchmark run 的根目录和预测文件位置。
@dataclass(frozen=True)
class BenchRunLayout:
    """集中生成 run/case artifact 路径，避免各层手拼目录。"""

    output_dir: Path
    predictions_path: Path
    baseline_predictions_path: Path | None

    def case_dir(self, instance_id: str) -> Path:
        return self.output_dir / "cases" / safe_id(instance_id)


def safe_id(value: str) -> str:

    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
