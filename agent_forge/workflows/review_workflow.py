import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ReviewFinding:
    """One deterministic code-review finding."""

    # severity values are high/medium/low/info.
    severity: str

    # File path or repo-level scope.
    path: str

    # Short issue title.
    title: str

    # Why this matters.
    detail: str

    def to_markdown(self) -> str:
        """Render one finding as a concise markdown bullet."""

        return f"- **{self.severity}** `{self.path}` {self.title}: {self.detail}"


@dataclass
class ReviewReport:
    """Structured review result for a git diff."""

    # User/reviewer supplied intent.
    task: str

    # Changed file paths from git diff.
    changed_files: list[str]

    # Raw diff stat.
    diff_stat: str

    # Deterministic findings.
    findings: list[ReviewFinding] = field(default_factory=list)

    # Review verdict.
    verdict: str = "needs_attention"

    def render(self) -> str:
        """Render the review report for CLI output and session artifacts."""

        lines = [
            "# Agent Forge Review Report",
            "",
            f"- task: {self.task}",
            f"- verdict: `{self.verdict}`",
            f"- changed_files: {len(self.changed_files)}",
            "",
            "## Changed Files",
            "",
        ]
        if self.changed_files:
            lines.extend(f"- `{path}`" for path in self.changed_files)
        else:
            lines.append("- none")

        lines.extend(["", "## Findings", ""])
        if self.findings:
            lines.extend(finding.to_markdown() for finding in self.findings)
        else:
            lines.append("- No deterministic blocker found. Still require domain validation for product behavior.")

        lines.extend(["", "## Diff Stat", "", "```text", self.diff_stat.strip() or "no diff", "```", ""])
        return "\n".join(lines)


def run_review(workspace: str, trace, task: str = "review current diff") -> ReviewReport:
    """Review current git diff with deterministic safety and quality checks.

    This is intentionally not a full PR bot. It provides the core production
    behavior that a coding-agent runtime needs: read the diff, classify risk,
    emit trace evidence, and produce a stable report that can be compared across
    runs or combined with an LLM reviewer later.
    """

    root = Path(workspace).resolve()
    changed_files = _git_lines(root, ["git", "diff", "--name-only"])
    diff_stat = _git_text(root, ["git", "diff", "--stat"])
    diff = _git_text(root, ["git", "diff", "--"])
    findings = _analyze_diff(changed_files, diff)
    verdict = _verdict(findings)
    report = ReviewReport(task, changed_files, diff_stat, findings, verdict)
    trace.set_run_context(task=task, stop_reason=verdict, final_answer=report.render())
    trace.add(
        1,
        "ReviewAgent",
        "review_diff",
        success=verdict != "blocked",
        changed_files=changed_files,
        findings=[finding.__dict__ for finding in findings],
        verdict=verdict,
    )
    return report


def _analyze_diff(changed_files: list[str], diff: str) -> list[ReviewFinding]:
    """Run deterministic checks that are useful before human/LLM review."""

    findings: list[ReviewFinding] = []
    if not changed_files:
        findings.append(ReviewFinding("info", "repo", "empty diff", "There is no code change to review."))
        return findings

    lowered = diff.lower()
    if ".env" in "\n".join(changed_files) or "api_key" in lowered or "authorization: bearer" in lowered:
        findings.append(
            ReviewFinding(
                "high",
                "repo",
                "possible secret exposure",
                "Diff mentions secret-like material; remove secrets before publishing.",
            )
        )
    if "subprocess.run" in diff and "shell=True" in diff:
        findings.append(
            ReviewFinding(
                "high",
                "repo",
                "shell execution risk",
                "shell=True in a coding-agent tool path needs a strong justification and sandbox boundary.",
            )
        )
    if any(path.startswith("agent_forge/safety/") for path in changed_files):
        findings.append(
            ReviewFinding(
                "medium",
                "agent_forge/safety",
                "safety policy changed",
                "Permission, sandbox, or guardrail changes require targeted validation.",
            )
        )
    if any(path.startswith("agent_forge/runtime/") for path in changed_files):
        findings.append(
            ReviewFinding(
                "medium",
                "agent_forge/runtime",
                "runtime control changed",
                "AgentLoop/state/control changes can affect long-running behavior and should be trace-verified.",
            )
        )
    if any(path.startswith("tests/") for path in changed_files):
        findings.append(
            ReviewFinding(
                "low",
                "tests",
                "tests changed",
                "Test changes should be interpreted with the related production-code diff.",
            )
        )
    if not any(path.startswith("tests/") or path.startswith("eval_cases/") for path in changed_files):
        findings.append(
            ReviewFinding(
                "low",
                "repo",
                "no test/eval diff",
                "For behavior changes, add or refresh at least one focused validation path.",
            )
        )
    return findings


def _verdict(findings: list[ReviewFinding]) -> str:
    """Turn findings into an overall review verdict."""

    if any(finding.severity == "high" for finding in findings):
        return "blocked"
    if any(finding.severity == "medium" for finding in findings):
        return "needs_attention"
    return "pass"


def _git_lines(root: Path, command: list[str]) -> list[str]:
    """Run git and return non-empty output lines."""

    text = _git_text(root, command)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _git_text(root: Path, command: list[str]) -> str:
    """Run a read-only git command and return stdout/stderr text."""

    try:
        result = subprocess.run(command, cwd=str(root), text=True, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
