"""轻量 Benchmark 流水线中需要由开发者负责的实验合同。

这里只定义什么可以比较、什么必须固定、哪些终态必须留在分母中；模型调用、路径、
环境变量和报告生成属于外层适配器。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


EXPECTED_OUTCOMES = ("resolved", "unresolved", "empty", "error", "incomplete")
RESERVED_RUNNER_ARGS = frozenset({"--api-key", "--instance-id", "--output-root"})
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class EvaluationContract:
    """一次 matched Pass@1 评测的最小、不可歧义输入。"""

    experiment_id: str
    comparison: str
    primary_metric: str
    case_ids: tuple[str, ...]
    ordered_case_ids_sha256: str
    shard_size: int
    benchmark_args: tuple[str, ...]
    variant_sources: tuple[tuple[str, str], ...]
    correctness_reruns: int
    terminal_outcomes: tuple[str, ...]
    analysis_in_pipeline: bool

    def __post_init__(self) -> None:
        if not self.experiment_id:
            raise ValueError("experiment_id is required")
        if self.comparison != "matched_pass_at_1":
            raise ValueError("only matched Pass@1 comparison is supported")
        if self.primary_metric != "official_resolved / planned":
            raise ValueError("primary metric must use the planned denominator")
        if self.correctness_reruns != 0:
            raise ValueError("correctness reruns are forbidden")
        if self.analysis_in_pipeline:
            raise ValueError("interpretation must stay outside the evaluation pipeline")
        if self.terminal_outcomes != EXPECTED_OUTCOMES:
            raise ValueError("all terminal outcomes must stay in the denominator")
        if not self.case_ids or len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("case_ids must be non-empty and unique")
        digest = hashlib.sha256("\n".join(self.case_ids).encode()).hexdigest()
        if digest != self.ordered_case_ids_sha256:
            raise ValueError("ordered Case identity drift")
        if self.shard_size <= 0 or len(self.case_ids) % self.shard_size:
            raise ValueError("shard_size must evenly divide the Case set")
        if not self.benchmark_args or "--evaluate" not in self.benchmark_args:
            raise ValueError("official evaluator must be enabled")
        if RESERVED_RUNNER_ARGS.intersection(self.benchmark_args):
            raise ValueError("runner-owned or secret flags cannot enter benchmark_args")
        names = [name for name, _ in self.variant_sources]
        if not names or len(names) != len(set(names)):
            raise ValueError("variant names must be non-empty and unique")
        if any(not _COMMIT.fullmatch(commit) for _, commit in self.variant_sources):
            raise ValueError("every variant must bind an exact source commit")

    @property
    def shards(self) -> tuple[tuple[str, ...], ...]:
        """按冻结 Case 顺序确定性分片，不根据运行结果重新分组。"""

        return tuple(
            tuple(self.case_ids[index : index + self.shard_size])
            for index in range(0, len(self.case_ids), self.shard_size)
        )

    def source_for(self, variant: str) -> str:
        """返回唯一变体源码；其余 Case 与 benchmark args 由合同共享。"""

        sources = dict(self.variant_sources)
        if variant not in sources:
            raise ValueError(f"unknown variant: {variant}")
        return sources[variant]
