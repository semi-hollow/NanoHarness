import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBHOOK_ROOT = ROOT / "examples/webhook_service_repo"
HANDLER_PATH = WEBHOOK_ROOT / "src/webhook_handler.py"
SIGNATURE_PATH = WEBHOOK_ROOT / "src/signature.py"
SECURITY_POLICY_PATH = WEBHOOK_ROOT / "docs/security_policy.md"
IDEMPOTENCY_TEST_PATH = WEBHOOK_ROOT / "tests/test_webhook_idempotency.py"
SIGNATURE_TEST_PATH = WEBHOOK_ROOT / "tests/test_signature_verification.py"

BUGGY_HANDLER = """from .signature import verify_signature


def handle_webhook(payload: dict, headers: dict, store, queue) -> dict:
    if not verify_signature(payload, headers):
        return {"status": "unauthorized", "code": 401}

    event_id = payload["event_id"]
    event_type = payload["type"]

    # BUG: duplicate event_id is not checked before side effects.
    store.insert_event(event_id, event_type, payload)
    queue.enqueue(event_id, event_type)

    return {"status": "accepted", "code": 200}
"""


def snapshot(paths: list[Path]) -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in paths if path.exists()}


def restore(files: dict[Path, str]) -> None:
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    for cache_dir in WEBHOOK_ROOT.rglob("__pycache__"):
        for child in cache_dir.glob("*"):
            child.unlink(missing_ok=True)
        cache_dir.rmdir()


def reset_webhook_fixture() -> None:
    HANDLER_PATH.write_text(BUGGY_HANDLER, encoding="utf-8")


def run_webhook_tests() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "examples/webhook_service_repo/tests"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


def load_trace(trace_path: Path) -> dict:
    return json.loads(trace_path.read_text(encoding="utf-8"))


def cleanup_trace_artifacts(trace_path: Path) -> None:
    for suffix in ("", ".usage.json", ".usage_report.md"):
        target = trace_path if not suffix else trace_path.with_suffix(suffix)
        target.unlink(missing_ok=True)
