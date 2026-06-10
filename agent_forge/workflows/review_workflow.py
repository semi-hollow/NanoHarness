import re
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

    This is the repository-level review gate. It provides the core behavior a
    coding-agent runtime needs before publishing changes: read the diff,
    classify risk, emit trace evidence, and produce a stable report that can be
    compared across runs or combined with an LLM reviewer later.
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

    added_by_file = _added_lines_by_file(diff)
    added_text = "\n".join(line for lines in added_by_file.values() for line in lines)
    if ".env" in "\n".join(changed_files) or _contains_secret_like_value(added_text):
        findings.append(
            ReviewFinding(
                "high",
                "repo",
                "possible secret exposure",
                "Diff mentions secret-like material; remove secrets before publishing.",
            )
        )
    shell_risk_paths = [
        path
        for path, lines in added_by_file.items()
        if not _is_test_path(path)
        and any(
            ("subprocess.run(" in line or "subprocess.Popen(" in line)
            and "shell=True" in line
            for line in lines
        )
    ]
    if shell_risk_paths:
        findings.append(
            ReviewFinding(
                "high",
                ", ".join(shell_risk_paths[:5]),
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


def _added_lines_by_file(diff: str) -> dict[str, list[str]]:
    """Return added diff lines grouped by new file path."""

    current = ""
    grouped: dict[str, list[str]] = {}
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current = parts[-1][2:] if len(parts) >= 4 and parts[-1].startswith("b/") else ""
            if current:
                grouped.setdefault(current, [])
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if current and line.startswith("+"):
            grouped.setdefault(current, []).append(line[1:])
    return grouped


def _is_test_path(path: str) -> bool:
    """Return whether a path is test-only evidence rather than runtime code."""

    lowered = path.lower()
    return lowered.startswith("tests/") or "/tests/" in lowered or lowered.endswith("_test.py") or "test_" in lowered


def _contains_secret_like_value(text: str) -> bool:
    """Return whether added text contains an actual secret-like value."""

    patterns = [
        r"Authorization:\s*Bearer\s+(?!\[redacted\]|your-|replace-)[A-Za-z0-9._\-]{16,}",
        r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[\"']?(?!your-|replace-|example|test)[A-Za-z0-9._\-]{16,}",
        r"sk-[A-Za-z0-9_\-]{16,}",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


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
