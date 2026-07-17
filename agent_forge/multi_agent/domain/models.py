from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# 核心数据：顺序多角色中一个角色的指令、工具和验收标记。
@dataclass(frozen=True)
class RoleSpec:
    """角色运行契约；revision 可使用比首轮更窄的工具集合。"""

    name: str
    role: str
    instructions: str
    allowed_tools: list[str] = field(default_factory=list)
    revision_allowed_tools: list[str] | None = None
    max_steps: int = 8
    output_artifact: str = "role_output"
    pass_markers: list[str] = field(default_factory=lambda: ["PASS"])
    revision_markers: list[str] = field(default_factory=lambda: ["NEEDS_REVISION"])
    blocked_markers: list[str] = field(default_factory=lambda: ["BLOCKED"])
    read_only: bool = False

    def to_dict(self) -> dict[str, Any]:

        return {
            "name": self.name,
            "role": self.role,
            "instructions": self.instructions,
            "allowed_tools": self.allowed_tools,
            "revision_allowed_tools": self.revision_allowed_tools,
            "max_steps": self.max_steps,
            "output_artifact": self.output_artifact,
            "pass_markers": self.pass_markers,
            "revision_markers": self.revision_markers,
            "blocked_markers": self.blocked_markers,
            "read_only": self.read_only,
        }


# 核心数据：顺序多角色 workflow 的角色顺序和修订预算。
@dataclass(frozen=True)
class AgentProfile:
    """声明 primary、reviewer、verifier 角色及默认 revision 上限。"""

    name: str
    description: str
    roles: list[RoleSpec]
    primary_role: str
    review_roles: list[str] = field(default_factory=list)
    verifier_roles: list[str] = field(default_factory=list)
    default_max_revision_rounds: int = 2

    def role_by_name(self, name: str) -> RoleSpec:

        for role in self.roles:
            if role.name == name:
                return role
        raise KeyError(f"profile {self.name} has no role named {name}")

    def ordered_review_roles(self) -> list[RoleSpec]:

        names = [*self.review_roles, *self.verifier_roles]
        return [self.role_by_name(name) for name in names]

    def to_dict(self) -> dict[str, Any]:

        return {
            "name": self.name,
            "description": self.description,
            "primary_role": self.primary_role,
            "review_roles": self.review_roles,
            "verifier_roles": self.verifier_roles,
            "default_max_revision_rounds": self.default_max_revision_rounds,
            "roles": [role.to_dict() for role in self.roles],
        }


# 核心数据：角色交给后续角色的显式文件 artifact。
@dataclass
class Artifact:
    """记录 artifact 身份、owner、类型、路径、摘要和修订轮次。"""

    id: str
    role: str
    kind: str
    path: Path
    summary: str = ""
    round_index: int = 0

    def to_dict(self) -> dict[str, Any]:

        return {
            "id": self.id,
            "role": self.role,
            "kind": self.kind,
            "path": str(self.path),
            "summary": self.summary,
            "round_index": self.round_index,
        }


# 核心数据：一个角色 round 的决定、输出和 artifact 引用。
@dataclass
class RoleRunResult:
    """区分运行状态、评审决定和最终文本，避免用一个字符串混合语义。"""

    role: str
    status: str
    decision: str
    artifact_ids: list[str]
    final_answer: str
    round_index: int
    error: str = ""

    def to_dict(self) -> dict[str, Any]:

        return {
            "role": self.role,
            "status": self.status,
            "decision": self.decision,
            "artifact_ids": self.artifact_ids,
            "final_answer": self.final_answer,
            "round_index": self.round_index,
            "error": self.error,
        }


# 核心数据：顺序多角色 workflow 的角色证据与最终状态。
@dataclass
class MultiAgentRunSummary:
    """聚合 profile、revision rounds、role results、artifacts 和最终交付路径。"""

    run_id: str
    task: str
    profile: str
    status: str = "running"
    revision_rounds: int = 0
    role_results: list[RoleRunResult] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    final_answer: str = ""
    summary_path: Path | None = None
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:

        return {
            "run_id": self.run_id,
            "task": self.task,
            "profile": self.profile,
            "status": self.status,
            "revision_rounds": self.revision_rounds,
            "role_results": [result.to_dict() for result in self.role_results],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "final_answer": self.final_answer,
            "summary_path": str(self.summary_path) if self.summary_path else "",
            "report_path": str(self.report_path) if self.report_path else "",
        }
