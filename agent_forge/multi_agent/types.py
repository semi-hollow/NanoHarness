from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RoleSpec:
    """One deterministic role in a coordinator-run multi-agent profile.

    A role is not an independent chatting peer. It is a bounded AgentLoop run
    with role instructions, an optional tool allowlist, a step budget, and an
    expected artifact. The coordinator decides when the role runs.
    """

    name: str
    role: str
    instructions: str
    allowed_tools: list[str] = field(default_factory=list)
    # Optional override for revision rounds. Some roles should gather evidence
    # on the first pass but revise only from reviewer/verifier artifacts later.
    # Keeping this explicit makes the orchestration explainable instead of
    # hiding profile-specific behavior inside the coordinator.
    revision_allowed_tools: list[str] | None = None
    max_steps: int = 8
    output_artifact: str = "role_output"
    pass_markers: list[str] = field(default_factory=lambda: ["PASS"])
    revision_markers: list[str] = field(default_factory=lambda: ["NEEDS_REVISION"])
    blocked_markers: list[str] = field(default_factory=lambda: ["BLOCKED"])
    read_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the role spec into reports and artifact indexes."""

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


@dataclass(frozen=True)
class AgentProfile:
    """A reusable coordinator workflow such as coding_fix or research_report."""

    name: str
    description: str
    roles: list[RoleSpec]
    primary_role: str
    review_roles: list[str] = field(default_factory=list)
    verifier_roles: list[str] = field(default_factory=list)
    default_max_revision_rounds: int = 2

    def role_by_name(self, name: str) -> RoleSpec:
        """Return one role or raise a clear profile configuration error."""

        for role in self.roles:
            if role.name == name:
                return role
        raise KeyError(f"profile {self.name} has no role named {name}")

    def ordered_review_roles(self) -> list[RoleSpec]:
        """Return reviewer/verifier roles in configured order."""

        names = [*self.review_roles, *self.verifier_roles]
        return [self.role_by_name(name) for name in names]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the profile definition."""

        return {
            "name": self.name,
            "description": self.description,
            "primary_role": self.primary_role,
            "review_roles": self.review_roles,
            "verifier_roles": self.verifier_roles,
            "default_max_revision_rounds": self.default_max_revision_rounds,
            "roles": [role.to_dict() for role in self.roles],
        }


@dataclass
class Artifact:
    """One explicit handoff artifact written by a role."""

    id: str
    role: str
    kind: str
    path: Path
    summary: str = ""
    round_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize path-bearing artifact metadata."""

        return {
            "id": self.id,
            "role": self.role,
            "kind": self.kind,
            "path": str(self.path),
            "summary": self.summary,
            "round_index": self.round_index,
        }


@dataclass
class RoleRunResult:
    """Result of one role's AgentLoop invocation."""

    role: str
    status: str
    decision: str
    artifact_ids: list[str]
    final_answer: str
    round_index: int
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize role execution data for summary JSON."""

        return {
            "role": self.role,
            "status": self.status,
            "decision": self.decision,
            "artifact_ids": self.artifact_ids,
            "final_answer": self.final_answer,
            "round_index": self.round_index,
            "error": self.error,
        }


@dataclass
class MultiAgentRunSummary:
    """Top-level coordinator run summary."""

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
        """Serialize the complete coordinator state."""

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
