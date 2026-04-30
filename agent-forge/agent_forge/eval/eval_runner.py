from dataclasses import asdict
from pathlib import Path
import os
import subprocess, sys
from .eval_case import EvalResult
from agent_forge.observability.metrics import summarize, summarize_trace_file


def run_case(case_dir: Path) -> EvalResult:
    case_id = case_dir.name
    cwd = Path.cwd()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(cwd) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run([sys.executable, str(case_dir / "verify.py")], cwd=str(cwd), env=env, capture_output=True, text=True)
    ok = proc.returncode == 0
    notes = (proc.stdout + proc.stderr).strip().replace("\n", " ")[:300]
    trace_file = case_dir / "trace.json"
    if trace_file.exists():
        metrics = summarize_trace_file(trace_file)
        trace_file.unlink()
        summary_file = case_dir / "summary.md"
        if summary_file.exists():
            summary_file.unlink()
    else:
        metrics = summarize([])
    return EvalResult(
        case_id,
        ok,
        ok,
        ok,
        not ok,
        int(metrics.get("handoff_count", 0)),
        int(metrics.get("tool_call_count", 0)),
        int(metrics.get("steps_count", 0)),
        notes,
        metrics,
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
        "# eval_report",
        "",
        f"- total: {total}",
        f"- passed: {passed}",
        f"- failed: {failed}",
        f"- pass rate: {pass_rate:.1f}%",
        f"- failed case list: {', '.join(failed_cases) if failed_cases else 'none'}",
        "",
        "|case_id|passed|task_success|test_pass|safety_violation|handoff_count|tool_call_count|steps_count|metrics|notes|",
        "|---|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for r in results:
        d = asdict(r)
        metric_text = ", ".join(f"{k}={v}" for k, v in (d["metrics"] or {}).items())
        lines.append(f"|{d['case_id']}|{d['passed']}|{d['task_success']}|{d['test_pass']}|{d['safety_violation']}|{d['handoff_count']}|{d['tool_call_count']}|{d['steps_count']}|{metric_text}|{d['notes']}|")
    Path("eval_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("eval_report.md generated")


if __name__ == "__main__":
    main()
