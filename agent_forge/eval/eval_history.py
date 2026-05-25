import json
import time
from pathlib import Path


class EvalHistory:
    """Append-only eval history for regression evidence.

    Production agent teams do not trust one green run. They keep pass rate,
    model, duration, and failure history over time. JSONL keeps this project
    lightweight while preserving that operational shape.
    """

    def __init__(self, path: str | Path = ".agent_forge/eval_history.jsonl"):
        """Create the parent directory for local eval history."""

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, results: list) -> None:
        """Append one aggregate eval run plus per-case summaries."""

        total = len(results)
        passed = sum(1 for result in results if result.passed)
        record = {
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
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
