import json
import time
from pathlib import Path


class EvalHistory:
    """Append-only eval history for regression evidence.

    Production agent teams do not trust one green run. They keep pass rate,
    model, duration, and failure history over time. JSONL keeps this project
    dependency-free while preserving that operational shape.
    """

    def __init__(self, path: str | Path = ".agent_forge/eval_history.jsonl"):
        """Create the parent directory for local eval history."""

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, results: list) -> dict:
        """Append one aggregate eval run plus per-case summaries."""

        record = self.build_record(results)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def build_record(self, results: list) -> dict:
        """Build the JSON record without writing it."""

        total = len(results)
        passed = sum(1 for result in results if result.passed)
        return {
            "created_at": time.time(),
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": (passed / total) if total else 0,
            "cases": [
                {
                    "case_id": result.case_id,
                    "passed": result.passed,
                    "notes": result.notes,
                    "metrics": result.metrics,
                }
                for result in results
            ],
        }

    def latest(self) -> dict:
        """Return the most recent eval record, or an empty dict."""

        if not self.path.exists():
            return {}
        last = ""
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = line
        if not last:
            return {}
        try:
            return json.loads(last)
        except json.JSONDecodeError:
            return {}

    def compare(self, current: dict, previous: dict | None = None) -> dict:
        """Compare current eval aggregate with the previous saved run."""

        previous = previous if previous is not None else self.latest()
        if not previous:
            return {
                "has_previous": False,
                "pass_rate_delta": 0.0,
                "new_failures": [],
                "fixed_failures": [],
                "summary": "no previous eval history",
            }

        current_failed = {
            case["case_id"]
            for case in current.get("cases", [])
            if not case.get("passed", False)
        }
        previous_failed = {
            case["case_id"]
            for case in previous.get("cases", [])
            if not case.get("passed", False)
        }
        delta = float(current.get("pass_rate", 0.0)) - float(previous.get("pass_rate", 0.0))
        return {
            "has_previous": True,
            "pass_rate_delta": delta,
            "new_failures": sorted(current_failed - previous_failed),
            "fixed_failures": sorted(previous_failed - current_failed),
            "summary": f"pass_rate_delta={delta:+.3f}",
        }
