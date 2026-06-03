import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .eval_case import EvalResult
from .eval_history import EvalHistory
from .flywheel import build_flywheel
from agent_forge.observability.metrics import summarize, summarize_trace_file


def parse_verify_json(stdout: str) -> dict:
    """Read the final JSON object emitted by a case `verify.py` script.

    Verify scripts can print debug text before their final JSON. Reading from
    the bottom makes the protocol tolerant to those logs.
    """

    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not (text.startswith("{") and text.endswith("}")):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def run_case(case_dir: Path) -> EvalResult:
    """Execute one eval case and convert its verify output into EvalResult."""

    case_id = case_dir.name
    task_file = case_dir / "task.md"
    task = task_file.read_text(encoding="utf-8").strip() if task_file.exists() else ""
    cwd = Path.cwd()
    env = os.environ.copy()
    # Make local package importable even when a verify script runs as a file.
    env["PYTHONPATH"] = str(cwd) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = f"{sys.executable} {case_dir / 'verify.py'}"
    proc = subprocess.run(
        [sys.executable, str(case_dir / "verify.py")],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    ok = proc.returncode == 0
    verify_data = parse_verify_json(proc.stdout)
    raw_notes = verify_data.get("notes") or (proc.stdout + proc.stderr)
    notes = str(raw_notes).strip().replace("\n", " ")[:300]
    trace_file = case_dir / "trace.json"
    if trace_file.exists():
        # Eval cases write temporary traces next to themselves. Convert to
        # metrics, then remove the trace so eval runs do not clutter the repo.
        metrics = summarize_trace_file(trace_file)
        trace_file.unlink()
        summary_file = case_dir / "summary.md"
        if summary_file.exists():
            summary_file.unlink()
    else:
        metrics = summarize([])
    task_success = bool(verify_data.get("task_success", ok))
    test_pass = bool(verify_data.get("test_pass", ok))
    safety_violation = bool(verify_data.get("safety_violation", False if ok else True))
    passed = ok and task_success and test_pass and not safety_violation

    return EvalResult(
        case_id,
        passed,
        task_success,
        test_pass,
        safety_violation,
        int(metrics.get("handoff_count", 0)),
        int(metrics.get("tool_call_count", 0)),
        int(metrics.get("agent_steps_count", 0)),
        int(metrics.get("trace_event_count", 0)),
        int(metrics.get("permission_denied_count", 0)),
        int(metrics.get("guardrail_block_count", 0)),
        int(metrics.get("failed_tool_call_count", 0)),
        notes,
        metrics,
        task,
        command,
    )


def main():
    """Run every eval case and write the markdown benchmark report.

    This is intentionally lightweight, not a SWE-bench clone. It gives enough
    evidence to discuss task success, test success, safety, tool count, and
    traceability without making the study project noisy.
    """

    root = Path("eval_cases")
    report_path = Path(os.getenv("AGENT_FORGE_EVAL_REPORT", ".agent_forge/eval_report.md"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Only folders with a verify script are eval cases. This avoids accidental
    # cache/build directories becoming benchmark rows after local test runs.
    results = [run_case(p) for p in sorted(root.iterdir()) if p.is_dir() and (p / "verify.py").exists()]
    total = len(results)
    passed = sum(r.passed for r in results)
    failed = total - passed
    failed_cases = [r.case_id for r in results if not r.passed]
    pass_rate = (passed / total * 100) if total else 0
    flywheel_rows, capability_summary = build_flywheel(results)
    lines = [
        "# Agent Forge Eval Report",
        "",
        "## Summary",
        "",
        "|Metric|Value|",
        "|---|---:|",
        f"|total_cases|{total}|",
        f"|passed_cases|{passed}|",
        f"|failed_cases|{failed}|",
        f"|pass_rate|{pass_rate:.1f}%|",
        "",
        f"- total: {total}",
        f"- passed: {passed}",
        f"- failed: {failed}",
        f"- pass rate: {pass_rate:.1f}%",
        f"- failed case list: {', '.join(failed_cases) if failed_cases else 'none'}",
        "",
        "## Capability Breakdown",
        "",
        "|capability|passed|failed|pass_rate|",
        "|---|---:|---:|---:|",
    ]
    for capability, counts in capability_summary.items():
        cap_passed = int(counts.get("pass", 0))
        cap_failed = int(counts.get("fail", 0))
        cap_total = cap_passed + cap_failed
        cap_rate = (cap_passed / cap_total * 100) if cap_total else 0.0
        lines.append(f"|{capability}|{cap_passed}|{cap_failed}|{cap_rate:.1f}%|")

    lines.extend(
        [
            "",
            "## Badcase Flywheel",
            "",
            "|case_id|status|capability|recommended_action|",
            "|---|---|---|---|",
        ]
    )
    for row in flywheel_rows:
        if row.status == "fail":
            lines.append(f"|{row.case_id}|{row.status}|{row.capability}|{row.recommended_action}|")
    if not failed_cases:
        lines.append("|none|pass|-|keep current cases as regression suite|")

    lines.extend(
        [
        "",
        "## Case Results",
        "",
        "|case_id|passed|task_success|test_pass|safety_violation|handoff_count|tool_call_count|agent_steps_count|trace_event_count|permission_denied_count|guardrail_block_count|failed_tool_call_count|notes|",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for r in results:
        d = asdict(r)
        lines.append(
            f"|{d['case_id']}|{d['passed']}|{d['task_success']}|{d['test_pass']}|"
            f"{d['safety_violation']}|{d['handoff_count']}|{d['tool_call_count']}|"
            f"{d['agent_steps_count']}|{d['trace_event_count']}|"
            f"{d['permission_denied_count']}|{d['guardrail_block_count']}|"
            f"{d['failed_tool_call_count']}|{d['notes']}|"
        )
    lines.extend(["", "## Failed Cases", "", ", ".join(failed_cases) if failed_cases else "none", "", "## Evidence", ""])
    for r in results:
        lines.append(f"- {r.case_id}: command=`{r.command}`, task_chars={len(r.task)}, verify={'pass' if r.passed else 'fail'}")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    EvalHistory().append(results)
    print(f"{report_path} generated")


if __name__ == "__main__":
    main()
