from dataclasses import asdict
from pathlib import Path
import json
import os
import subprocess, sys
from .eval_case import EvalResult
from agent_forge.observability.metrics import summarize, summarize_trace_file


def parse_verify_json(stdout: str) -> dict:
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
    case_id = case_dir.name
    task_file = case_dir / "task.md"
    task = task_file.read_text(encoding="utf-8").strip() if task_file.exists() else ""
    cwd = Path.cwd()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(cwd) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = f"{sys.executable} {case_dir / 'verify.py'}"
    proc = subprocess.run([sys.executable, str(case_dir / "verify.py")], cwd=str(cwd), env=env, capture_output=True, text=True)
    ok = proc.returncode == 0
    verify_data = parse_verify_json(proc.stdout)
    raw_notes = verify_data.get("notes") or (proc.stdout + proc.stderr)
    notes = str(raw_notes).strip().replace("\n", " ")[:300]
    trace_file = case_dir / "trace.json"
    if trace_file.exists():
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
    root = Path("eval_cases")
    results = [run_case(p) for p in sorted(root.iterdir()) if p.is_dir()]
    total = len(results)
    passed = sum(r.passed for r in results)
    failed = total - passed
    failed_cases = [r.case_id for r in results if not r.passed]
    pass_rate = (passed / total * 100) if total else 0
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
        "## Case Results",
        "",
        "|case_id|passed|task_success|test_pass|safety_violation|handoff_count|tool_call_count|agent_steps_count|trace_event_count|permission_denied_count|guardrail_block_count|failed_tool_call_count|notes|",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in results:
        d = asdict(r)
        lines.append(f"|{d['case_id']}|{d['passed']}|{d['task_success']}|{d['test_pass']}|{d['safety_violation']}|{d['handoff_count']}|{d['tool_call_count']}|{d['agent_steps_count']}|{d['trace_event_count']}|{d['permission_denied_count']}|{d['guardrail_block_count']}|{d['failed_tool_call_count']}|{d['notes']}|")
    lines.extend(["", "## Failed Cases", "", ", ".join(failed_cases) if failed_cases else "none", "", "## Evidence", ""])
    for r in results:
        lines.append(f"- {r.case_id}: command=`{r.command}`, task_chars={len(r.task)}, verify={'pass' if r.passed else 'fail'}")
    Path("eval_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("eval_report.md generated")


if __name__ == "__main__":
    main()
