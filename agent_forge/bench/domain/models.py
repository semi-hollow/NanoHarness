from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# 核心数据：从 SWE-bench dataset 边界归一化后的单题输入。
@dataclass
class BenchCase:
    """Agent 可见任务和官方隐藏字段的内部载体。

    ``instance_id/repo/base_commit/problem_statement`` 是必填执行输入；``hints_text``
    可进入任务；``test_patch`` 和 ``raw`` 中的 gold/evaluation 字段只供
    Harness 使用。
    """

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    test_patch: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "BenchCase":

        instance_id = str(data.get("instance_id") or data.get("id") or "")
        repo = str(data.get("repo") or data.get("repository") or "")
        base_commit = str(data.get("base_commit") or data.get("commit") or "")
        problem = str(data.get("problem_statement") or data.get("issue") or data.get("task") or "")
        if not instance_id or not repo or not base_commit or not problem:
            missing = [
                name
                for name, value in {
                    "instance_id": instance_id,
                    "repo": repo,
                    "base_commit": base_commit,
                    "problem_statement": problem,
                }.items()
                if not value
            ]
            raise ValueError(f"SWE-bench case is missing required fields: {', '.join(missing)}")
        return cls(
            instance_id=instance_id,
            repo=repo,
            base_commit=base_commit,
            problem_statement=problem,
            hints_text=str(data.get("hints_text") or ""),
            test_patch=str(data.get("test_patch") or ""),
            raw=dict(data),
        )


# 核心数据：单题 candidate patch、本地验证、官方评测与诊断的最终事实。
@dataclass
class BenchCaseResult:
    """一个 case 的 artifact 位置和分层 correctness evidence。

    ``status`` 是 Runtime 结果；``patch_chars`` 只说明存在 candidate diff；
    ``local_validation_status`` 与 ``official_evaluation_status`` 是独立证据层；
    ``failure_class/diagnosis/evidence/next_actions`` 必须在最终评测后写入。
    """

    instance_id: str
    repo: str
    workspace: Path
    trace_path: Path
    usage_report_path: Path | None
    patch_path: Path
    status: str
    final_answer: str
    patch_chars: int = 0
    error: str = ""
    evaluation_status: str = "not_evaluated"
    local_validation_status: str = "not_run"
    local_validation_evidence: list[str] = field(default_factory=list)
    official_evaluation_status: str = "not_evaluated"
    official_evaluation_report_path: str = ""
    official_evaluation_detail: str = ""
    failure_class: str = ""
    diagnosis: str = ""
    diagnosis_evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:

        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "workspace": str(self.workspace),
            "trace_path": str(self.trace_path),
            "usage_report_path": str(self.usage_report_path) if self.usage_report_path else "",
            "patch_path": str(self.patch_path),
            "status": self.status,
            "final_answer": self.final_answer,
            "patch_chars": self.patch_chars,
            "error": self.error,
            "evaluation_status": self.evaluation_status,
            "local_validation_status": self.local_validation_status,
            "local_validation_evidence": self.local_validation_evidence,
            "official_evaluation_status": self.official_evaluation_status,
            "official_evaluation_report_path": self.official_evaluation_report_path,
            "official_evaluation_detail": self.official_evaluation_detail,
            "failure_class": self.failure_class,
            "diagnosis": self.diagnosis,
            "diagnosis_evidence": self.diagnosis_evidence,
            "next_actions": self.next_actions,
        }


# 核心数据：整个 benchmark run 的实验身份、资源配置和 per-case 证据。
@dataclass
class BenchRunSummary:
    """可写入 ``results.json``、report 和 scorecard 的 run truth model。

    前半部分保存 dataset/provider/agent/sampling 身份；中间保存 Skill、Memory、
    execution environment 与预算；后半部分保存 baseline、official evaluator、case 结果
    和 notes。Renderer 只能消费这里的事实，不能把 candidate patch 推断成 solved。
    """

    # 实验身份和输出根路径。
    run_id: str
    dataset_name: str
    split: str
    provider: str
    model: str
    output_dir: Path
    predictions_path: Path
    # Agent workflow、模型采样和可见工具身份。
    temperature: float = 0.0
    thinking_mode: str = "disabled"
    reasoning_effort: str | None = None
    agent_mode: str = "single"
    profile: str = ""
    max_revision_rounds: int = 0
    tool_routing_mode: str = "task-aware"
    # Skill snapshot 与执行环境身份。
    skill_mode: str = "auto"
    skill_names: list[str] = field(default_factory=list)
    skill_manifest_sha256: str = ""
    execution_mode: str = "local"
    network_policy: str = "deny"
    keep_worktree: bool = False
    container_runtime: str = "docker"
    container_image: str = "python:3.11-slim"
    container_cpus: float = 1.0
    container_memory: str = "1g"
    container_pids_limit: int = 256
    container_read_only: bool = True
    # Runtime 预算和长期记忆冻结输入。
    max_steps: int = 0
    max_context_chars: int = 0
    max_prompt_tokens: int = 0
    reserved_output_tokens: int = 0
    max_tool_calls_per_turn: int = 0
    cost_budget_usd: float | None = None
    timeout_seconds: float = 0.0
    memory_namespace: str = ""
    memory_recall_limit: int = 0
    memory_snapshot_sha256: str = ""
    # 对照实验、official evaluator 和 per-case 最终结果。
    baseline_predictions_path: Path | None = None
    variant_comparisons: dict[str, dict[str, Any]] = field(default_factory=dict)
    official_eval_command: list[str] = field(default_factory=list)
    official_eval_exit_code: int | None = None
    official_eval_output: str = ""
    official_eval_report_path: str = ""
    official_eval_warnings: list[str] = field(default_factory=list)
    case_results: list[BenchCaseResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:

        return {
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "split": self.split,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "thinking_mode": self.thinking_mode,
            "reasoning_effort": self.reasoning_effort,
            "agent_mode": self.agent_mode,
            "profile": self.profile,
            "max_revision_rounds": self.max_revision_rounds,
            "tool_routing_mode": self.tool_routing_mode,
            "skill_mode": self.skill_mode,
            "skill_names": self.skill_names,
            "skill_manifest_sha256": self.skill_manifest_sha256,
            "execution_mode": self.execution_mode,
            "network_policy": self.network_policy,
            "keep_worktree": self.keep_worktree,
            "container_runtime": self.container_runtime,
            "container_image": self.container_image,
            "container_cpus": self.container_cpus,
            "container_memory": self.container_memory,
            "container_pids_limit": self.container_pids_limit,
            "container_read_only": self.container_read_only,
            "max_steps": self.max_steps,
            "max_context_chars": self.max_context_chars,
            "max_prompt_tokens": self.max_prompt_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "max_tool_calls_per_turn": self.max_tool_calls_per_turn,
            "cost_budget_usd": self.cost_budget_usd,
            "timeout_seconds": self.timeout_seconds,
            "memory_namespace": self.memory_namespace,
            "memory_recall_limit": self.memory_recall_limit,
            "memory_snapshot_sha256": self.memory_snapshot_sha256,
            "output_dir": str(self.output_dir),
            "predictions_path": str(self.predictions_path),
            "baseline_predictions_path": (
                str(self.baseline_predictions_path) if self.baseline_predictions_path else ""
            ),
            "variant_comparisons": self.variant_comparisons,
            "official_eval_command": self.official_eval_command,
            "official_eval_exit_code": self.official_eval_exit_code,
            "official_eval_output": self.official_eval_output,
            "official_eval_report_path": self.official_eval_report_path,
            "official_eval_warnings": self.official_eval_warnings,
            "case_results": [result.to_dict() for result in self.case_results],
            "notes": self.notes,
        }
