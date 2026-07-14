from __future__ import annotations

from .domain.models import AgentProfile, RoleSpec

CODING_READ_TOOLS = ["list_files", "read_file", "grep", "grep_search", "git_status", "git_diff", "diagnostics"]
CODING_WRITE_TOOLS = [*CODING_READ_TOOLS, "apply_patch", "write_file", "run_command"]
RESEARCH_TOOLS = ["list_files", "read_file", "grep", "grep_search", "forge.web_search", "forge.web_fetch"]


def coding_fix_profile() -> AgentProfile:

    return AgentProfile(
        name="coding_fix",
        description="Coordinator-driven coding repair with implementer, reviewer, verifier, and bounded revisions.",
        primary_role="Implementer",
        review_roles=["Reviewer"],
        verifier_roles=["Verifier"],
        default_max_revision_rounds=2,
        roles=[
            RoleSpec(
                name="Implementer",
                role="coding implementer",
                instructions=(
                    "Inspect the task and repository evidence, make the smallest safe code change, "
                    "and run a focused validation command when allowed. Do not edit tests unless the task "
                    "is explicitly about test infrastructure. If you are revising, address the review or "
                    "verification artifact directly. For SWE-bench-style tasks, prefer read_file/grep_search "
                    "for inspection, apply_patch for the first concrete fix, and diagnostics for validation; "
                    "avoid shell-style run_command exploration."
                ),
                allowed_tools=CODING_WRITE_TOOLS,
                max_steps=20,
                output_artifact="implementation_report",
            ),
            RoleSpec(
                name="Reviewer",
                role="read-only patch reviewer",
                instructions=(
                    "Review the current diff and implementation artifact. Do not modify files. "
                    "Start your answer with exactly one marker: PASS, NEEDS_REVISION, or BLOCKED. "
                    "Then explain findings with file/command evidence. Use NEEDS_REVISION when a concrete "
                    "code change is required; use BLOCKED only for missing evidence or unavailable tools."
                ),
                allowed_tools=CODING_READ_TOOLS,
                max_steps=5,
                output_artifact="review_report",
                read_only=True,
            ),
            RoleSpec(
                name="Verifier",
                role="validation runner",
                instructions=(
                    "Inspect the candidate patch and run the smallest allowed validation command if one is clearly available. "
                    "Start your answer with exactly one marker: PASS, NEEDS_REVISION, or BLOCKED. "
                    "Use PASS when the patch is present, review evidence is sound, and available diagnostics do not show a regression. "
                    "Use NEEDS_REVISION when validation fails for a likely fixable code reason. "
                    "Use BLOCKED when external benchmark validation is unavailable; do not spend the final turn requesting another tool."
                ),
                allowed_tools=["read_file", "git_status", "git_diff", "diagnostics"],
                max_steps=8,
                output_artifact="verification_report",
            ),
        ],
    )


def research_report_profile() -> AgentProfile:

    return AgentProfile(
        name="research_report",
        description="Research draft with skeptical review and fact verification through explicit artifacts.",
        primary_role="Researcher",
        review_roles=["SkepticalReviewer"],
        verifier_roles=["FactVerifier"],
        default_max_revision_rounds=2,
        roles=[
            RoleSpec(
                name="Researcher",
                role="source-backed research writer",
                instructions=(
                    "Draft a concise research report with citations or source notes. Use available local/MCP "
                    "web tools when configured. If live search or fetch is unavailable, clearly mark source "
                    "limitations and avoid pretending to have checked current sources. On revision rounds, "
                    "focus on directly addressing reviewer and verifier artifacts rather than collecting "
                    "more evidence."
                ),
                allowed_tools=RESEARCH_TOOLS,
                revision_allowed_tools=[],
                max_steps=8,
                output_artifact="research_draft",
            ),
            RoleSpec(
                name="SkepticalReviewer",
                role="skeptical evidence reviewer",
                instructions=(
                    "Review the draft for unsupported claims, weak sources, contradictions, stale facts, and "
                    "missing caveats. Do not add new claims. Start with PASS, NEEDS_REVISION, or BLOCKED."
                ),
                allowed_tools=[],
                max_steps=3,
                output_artifact="skeptical_review",
                read_only=True,
            ),
            RoleSpec(
                name="FactVerifier",
                role="claim verifier",
                instructions=(
                    "Verify major claims against the available artifacts and sources. If live source access is "
                    "missing, say so explicitly. Start with PASS, NEEDS_REVISION, or BLOCKED."
                ),
                allowed_tools=[],
                max_steps=3,
                output_artifact="fact_check_report",
                read_only=True,
            ),
        ],
    )


def list_profiles() -> list[str]:

    return ["coding_fix", "research_report"]


def get_profile(name: str) -> AgentProfile:

    profiles = {
        "coding_fix": coding_fix_profile,
        "research_report": research_report_profile,
    }
    try:
        return profiles[name]()
    except KeyError as exc:
        raise ValueError(f"unsupported multi-agent profile: {name}") from exc
