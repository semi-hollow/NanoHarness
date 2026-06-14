from collections import Counter, defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class FlywheelRow:
    """One eval case converted into iteration guidance."""

    case_id: str
    status: str
    capability: str
    recommended_action: str


def infer_capability(case_id: str, task: str = "") -> str:
    """Infer a coarse capability label from case id and task text."""

    text = f"{case_id} {task}".lower()
    if "context" in text or "retrieval" in text or "symbol" in text:
        return "context"
    if "dangerous" in text or "sandbox" in text or "secret" in text or "approval" in text:
        return "safety"
    if "webhook" in text:
        return "coding_benchmark"
    if "tool" in text or "arguments" in text or "command" in text:
        return "tool_governance"
    if "workflow" in text or "multi" in text or "review" in text:
        return "orchestration"
    return "agent_loop"


def build_flywheel(results: list) -> tuple[list[FlywheelRow], dict[str, dict[str, int]]]:
    """Convert eval results into badcase and capability summaries.

    This is the repository-local version of a data flywheel: each failed case becomes
    an actionable queue item, and each pass/fail updates capability-level health.
    """

    rows: list[FlywheelRow] = []
    summary: dict[str, Counter] = defaultdict(Counter)
    for result in results:
        capability = infer_capability(result.case_id, getattr(result, "task", ""))
        status = "pass" if result.passed else "fail"
        summary[capability][status] += 1
        if result.passed:
            action = "keep as regression coverage"
        elif result.safety_violation:
            action = "tighten policy or sandbox and add a regression case"
        elif not result.test_pass:
            action = "inspect trace, patch runtime/tool behavior, rerun eval"
        else:
            action = "classify badcase and improve prompt/context/tool schema"
        rows.append(FlywheelRow(result.case_id, status, capability, action))
    return rows, {cap: dict(counts) for cap, counts in sorted(summary.items())}
