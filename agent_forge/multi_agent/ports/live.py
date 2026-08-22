"""Multi-Agent Application 依赖的全部外部能力契约。

这里没有实现逻辑。折叠后只看四组 Port：1 workspace/artifact；2 worker-bound LIVE；
3 Worker/Finalizer/Replanner；4 事件流。Application 只依赖这些 Protocol。
"""

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
    WorkerHandoff,
)
from ..domain.live_handoff import LiveHandoffEvent
from ..domain.planning import PlanningDecision


# region 1. Workspace 与 Artifact Ports：隔离 Git 副作用和持久化格式
class FanoutWorkspacePort(Protocol):
    """Application 合并 worker candidate diff 所需的 Git 能力。"""

    def head(self) -> str:
        """返回集成 workspace 当前 commit。"""

    def status(self) -> str:
        """返回非空 dirty 状态摘要。"""

    def diff(self) -> str:
        """返回当前 workspace 的 unified diff。"""

    def apply_unified_diff(
        self,
        diff_text: str,
        *,
        check_only: bool,
    ) -> tuple[bool, str]:
        """检查或应用 ``git diff`` 产生的 unified diff。"""


class FanoutArtifactPort(Protocol):
    """Fanout checkpoint、summary 和 diff 文件的持久化边界。"""

    def write_plan(self, plan: FanoutPlan) -> str:
        """保存经过验证的计划。"""

    def write_checkpoint(self, checkpoint: FanoutCheckpoint) -> str:
        """原子保存当前恢复点。"""

    def write_integrated_diff(self, diff_text: str) -> str:
        """保存所有成功 worker 合并后的 unified diff。"""

    def write_summary(self, summary: LiveFanoutSummary) -> None:
        """保存 JSON summary 和人类可读报告。"""

    def load_resume(self, path: str) -> dict[str, Any]:
        """读取 summary/checkpoint 并返回未信任边界数据。"""

    def read_text(self, path: str) -> str:
        """读取结果中已经记录的文本 artifact。"""

    def append_coordination(self, record: dict[str, Any]) -> str:
        """追加一条可审计 coordination JSONL 事实。"""
# endregion 1. Workspace 与 Artifact Ports 结束


# region 2. Worker-bound LIVE Port：只暴露绑定身份后的 publish/drain
class LiveWorkerContextPort(Protocol):
    """绑定单一 Worker 身份与 attempt 的最小 coordination 能力。"""

    task_id: str
    worker_attempt_id: int

    def publish(
        self,
        *,
        event_type: str,
        target_task_id: str,
        semantic_key: str,
        version: int,
        summary: str,
        evidence: list[str],
        caused_by_event_id: str = "",
    ) -> LiveHandoffEvent:
        """由 Runtime 注入 publisher/generation/attempt 后发布事实。"""

    def drain_mailbox(self, *, boundary: str) -> list[LiveHandoffEvent]:
        """在真实 AgentLoop 安全边界消费当前 attempt 的事实。"""
# endregion 2. Worker-bound LIVE Port 结束


# region 3. Worker、Finalizer 与 Replanner Ports：执行端和模型端均可替换但语义固定
class FanoutWorkerPort(Protocol):
    """隔离 AgentLoop worker 和 finalizer 的执行边界。"""

    def run_worker(
        self,
        task: SubagentTask,
        batch_index: int,
        base_diff_text: str,
        dependency_handoffs: list[WorkerHandoff],
        attempt: int,
        coordination: LiveWorkerContextPort | None = None,
    ) -> LiveSubagentResult:
        """在隔离 workspace 中执行一个真实 AgentLoop。"""

    def run_finalizer(
        self,
        plan: FanoutPlan,
        results: list[LiveSubagentResult],
    ) -> FinalizerResult:
        """运行只读整合验证器。"""

    def validate_recovery_diffs(self, diffs: list[tuple[str, str]]) -> str:
        """在临时 workspace 中重放恢复所需的 unified diff。"""


class FanoutReplannerPort(Protocol):
    """一次剩余任务重规划所需的最小模型边界。"""

    def replan(
        self,
        *,
        goal: str,
        current_plan: FanoutPlan,
        completed_handoffs: list[WorkerHandoff],
        failed_results: list[LiveSubagentResult],
    ) -> PlanningDecision:
        """返回未完成工作的替换提议；Coordinator 再做确定性校验。"""
# endregion 3. Worker、Finalizer 与 Replanner Ports 结束


# region 4. 事件流 Port：与 Runtime 共用同一 Trace sink
class LiveFanoutEvents(EventSink, Protocol):
    """别名，强调 fanout 与 Runtime 共用同一事实流端口。"""
# endregion 4. 事件流 Port 结束
