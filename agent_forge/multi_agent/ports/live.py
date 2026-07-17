"""Live fanout 对 Git、文件和 worker runtime 的外部能力契约。"""

from __future__ import annotations

from typing import Any, Protocol

from agent_forge.runtime.ports.events import EventSink

from ..domain.fanout import SubagentTask
from ..domain.live import (
    FanoutCheckpoint,
    FanoutPlan,
    FinalizerResult,
    LiveFanoutSummary,
    LiveSubagentResult,
)


class FanoutWorkspacePort(Protocol):
    """Application 合并 worker patch 所需的 Git 能力。"""

    def head(self) -> str:
        """返回集成 workspace 当前 commit。"""

    def status(self) -> str:
        """返回非空 dirty 状态摘要。"""

    def diff(self) -> str:
        """返回当前 candidate patch。"""

    def apply_patch(self, patch: str, *, check_only: bool) -> tuple[bool, str]:
        """检查或应用一个 Git patch。"""


class FanoutArtifactPort(Protocol):
    """Fanout checkpoint、summary 和 patch 的文件边界。"""

    def write_plan(self, plan: FanoutPlan) -> str:
        """保存经过验证的计划。"""

    def write_checkpoint(self, checkpoint: FanoutCheckpoint) -> str:
        """原子保存当前恢复点。"""

    def write_integration_patch(self, patch: str) -> str:
        """保存集成 workspace 的 candidate patch。"""

    def write_summary(self, summary: LiveFanoutSummary) -> None:
        """保存 JSON summary 和人类可读报告。"""

    def load_resume(self, path: str) -> dict[str, Any]:
        """读取 summary/checkpoint 并返回未信任边界数据。"""

    def read_text(self, path: str) -> str:
        """读取结果中已经记录的文本 artifact。"""


class FanoutWorkerPort(Protocol):
    """隔离 AgentLoop worker 和 finalizer 的执行边界。"""

    def run_worker(
        self,
        task: SubagentTask,
        batch_index: int,
        base_patch: str,
    ) -> LiveSubagentResult:
        """在隔离 workspace 中执行一个真实 AgentLoop。"""

    def run_finalizer(
        self,
        goal: str,
        results: list[LiveSubagentResult],
    ) -> FinalizerResult:
        """运行只读整合验证器。"""

    def validate_recovery_patches(self, patches: list[tuple[str, str]]) -> str:
        """在临时 workspace 中重放恢复 patch。"""


class LiveFanoutEvents(EventSink, Protocol):
    """别名，强调 fanout 与 Runtime 共用同一事实流端口。"""
