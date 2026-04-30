from dataclasses import asdict
from pathlib import Path
import subprocess, sys
from .eval_case import EvalResult


def run_case(case_dir: Path) -> EvalResult:
    case_id = case_dir.name
    proc = subprocess.run([sys.executable, str(case_dir / "verify.py")], cwd=str(Path.cwd()), capture_output=True, text=True)
    ok = proc.returncode == 0
    notes = (proc.stdout + proc.stderr).strip()[:300]
    return EvalResult(case_id, ok, ok, ok, not ok, 0, 0, 0, notes)


def main():
    root = Path("eval_cases")
    results = [run_case(p) for p in sorted(root.iterdir()) if p.is_dir()]
    lines = ["# eval_report", "", "|case_id|passed|task_success|test_pass|safety_violation|handoff_count|tool_call_count|steps_count|notes|", "|---|---|---|---|---|---:|---:|---:|---|"]
    for r in results:
        d = asdict(r)
        lines.append(f"|{d['case_id']}|{d['passed']}|{d['task_success']}|{d['test_pass']}|{d['safety_violation']}|{d['handoff_count']}|{d['tool_call_count']}|{d['steps_count']}|{d['notes']}|")
    Path("eval_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("eval_report.md generated")


if __name__ == "__main__":
    main()
