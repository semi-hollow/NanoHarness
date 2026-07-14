"""顺序多角色 Coordinator 的外部能力契约。"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent_forge.observability.domain.event import TraceEventType
from agent_forge.runtime.config import RuntimeConfig

from ..domain.models import Artifact, MultiAgentRunSummary, RoleSpec


class CoordinatorEventSink(Protocol):
    run_id: str

    def set_run_context(
        self,
        task: str = "",
        stop_reason: str = "",
        final_answer: str = "",
    ) -> None:
        """更新 coordinator 顶层运行事实。"""

    def record_event(
        self,
        *,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        success: bool = True,
        error: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """追加一个 coordinator event。"""


class RoleArtifactPort(Protocol):
    artifacts: list[Artifact]

    def write_role_artifact(
        self,
        role: RoleSpec,
        content: str,
        round_index: int,
    ) -> Artifact:
        """保存一个角色交接 artifact。"""

    def write_text_artifact(
        self,
        role_name: str,
        kind: str,
        content: str,
        round_index: int = 0,
    ) -> Artifact:
        """保存 coordinator 生成的 artifact。"""

    def write_summary(self, summary: MultiAgentRunSummary) -> tuple[object, object]:
        """保存 JSON 和 Markdown summary。"""

    def render_handoff_context(self, limit_chars: int = 12000) -> str:
        """读取最近 artifact 作为下一个角色的显式上下文。"""


class RoleRunnerPort(Protocol):
    def run_role(
        self,
        *,
        config: RuntimeConfig,
        allowed_tools: list[str],
        task: str,
        agent_name: str,
    ) -> str:
        """使用规范 Runtime 执行一个受工具限制的角色。"""


class CandidatePatchPort(Protocol):
    def exists(self) -> bool:
        """返回集成 workspace 是否存在 candidate patch。"""
