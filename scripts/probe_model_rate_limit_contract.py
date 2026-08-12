#!/usr/bin/env python3
"""通过重复原生工具能力探测来确认 provider 容量。

每个子探测执行相同的两次请求合成工具往返；重试、传输错误、fallback 或
response.model 漂移都会使本轮失败。脚本不消耗 benchmark Case，不执行真实工具，
不记录凭据或模型自由文本。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


CAPABILITY_PROBE = Path(__file__).with_name("probe_model_tool_contract.py")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_endpoint_identity(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    endpoint = f"{parsed.scheme}://{parsed.hostname or ''}"
    if parsed.port:
        endpoint += f":{parsed.port}"
    return endpoint + parsed.path.rstrip("/"), hashlib.sha256(
        base_url.encode("utf-8")
    ).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _expected_identity(
    preflight: dict[str, Any],
    *,
    provider: str,
    model: str,
    base_url: str,
    thinking: str,
    reasoning_effort: str,
    max_attempts: int | None = None,
) -> str:
    endpoint, base_url_sha256 = _safe_endpoint_identity(base_url)
    expected = {
        "schema_version": 1,
        "status": "passed",
        "provider": provider,
        "requested_model": model,
        "credential_source": "OPENCODE_GO_API_KEY",
        "base_url_origin_path": endpoint,
        "base_url_sha256": base_url_sha256,
        "thinking_mode": thinking,
        "reasoning_effort": reasoning_effort,
        "tool_call_source": "native",
        "tool_call_count": 1,
        "tool_arguments_match": True,
        "round_trip_completed": True,
        "fallback_used": False,
        "attempts_per_call": [1, 1],
        "error_codes": [],
        "error_code": "",
    }
    if max_attempts is not None and (max_attempts != 2 or "max_attempts" in preflight):
        expected["max_attempts"] = max_attempts
    if any(preflight.get(key) != value for key, value in expected.items()):
        raise RuntimeError("capability preflight identity/transport drift")
    observed = str(preflight.get("observed_response_model") or "")
    if not observed or preflight.get("round_trip_observed_response_model") != observed:
        raise RuntimeError("capability preflight observed-model drift")
    return observed


def _round_failure(
    evidence: dict[str, Any],
    *,
    expected_observed_model: str,
    provider: str,
    model: str,
    max_attempts: int | None = None,
) -> str:
    expected = {
        "status": "passed",
        "provider": provider,
        "requested_model": model,
        "credential_source": "OPENCODE_GO_API_KEY",
        "observed_response_model": expected_observed_model,
        "round_trip_observed_response_model": expected_observed_model,
        "tool_call_source": "native",
        "tool_call_count": 1,
        "tool_arguments_match": True,
        "round_trip_completed": True,
        "fallback_used": False,
        "attempts_per_call": [1, 1],
        "error_codes": [],
        "error_code": "",
    }
    if max_attempts is not None and (max_attempts != 2 or "max_attempts" in evidence):
        expected["max_attempts"] = max_attempts
    drift = sorted(key for key, value in expected.items() if evidence.get(key) != value)
    return "" if not drift else "round evidence drift: " + ", ".join(drift)


def _positive_int(value: str) -> int:
    """解析正整数参数，使容量预检的尝试上限可被冻结。"""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    """构造可单测的容量探测参数边界。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--thinking", default="enabled")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-attempts", type=_positive_int, default=2)
    parser.add_argument("--round-trips", type=int, default=4)
    parser.add_argument("--capability-preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if args.round_trips < 2:
        raise ValueError("--round-trips must be at least 2")
    if args.output.exists():
        raise RuntimeError("capacity-probe output already exists")

    preflight = _read_json(args.capability_preflight, "capability preflight")
    expected_observed_model = _expected_identity(
        preflight,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        thinking=args.thinking,
        reasoning_effort=args.reasoning_effort,
        max_attempts=args.max_attempts,
    )
    rounds_root = args.output.with_suffix("")
    if rounds_root.exists():
        raise RuntimeError("capacity-probe rounds root already exists")
    rounds_root.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    started_at = _utc_now()
    failure = ""
    for ordinal in range(1, args.round_trips + 1):
        round_path = rounds_root / f"round-{ordinal:02d}.json"
        argv = [
            sys.executable,
            str(CAPABILITY_PROBE),
            "--provider",
            args.provider,
            "--model",
            args.model,
            "--base-url",
            args.base_url,
            "--thinking",
            args.thinking,
            "--reasoning-effort",
            args.reasoning_effort,
            "--timeout",
            str(args.timeout),
            "--max-attempts",
            str(args.max_attempts),
            "--output",
            str(round_path),
        ]
        process = subprocess.run(argv, check=False, text=True, capture_output=True)
        evidence = _read_json(round_path, f"capacity round {ordinal}")
        failure = _round_failure(
            evidence,
            expected_observed_model=expected_observed_model,
            provider=args.provider,
            model=args.model,
            max_attempts=args.max_attempts,
        )
        if process.returncode != 0 and not failure:
            failure = f"capability probe exited {process.returncode}"
        records.append(
            {
                "ordinal": ordinal,
                "exit_code": process.returncode,
                "artifact": str(round_path),
                "artifact_sha256": _sha256(round_path),
                "attempts_per_call": evidence.get("attempts_per_call"),
                "error_codes": evidence.get("error_codes"),
                "observed_response_model": evidence.get("observed_response_model"),
                "passed": not failure,
            }
        )
        if failure:
            break

    passed = not failure and len(records) == args.round_trips
    result = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "provider": args.provider,
        "requested_model": args.model,
        "credential_source": "OPENCODE_GO_API_KEY",
        "base_url_origin_path": _safe_endpoint_identity(args.base_url)[0],
        "base_url_sha256": _safe_endpoint_identity(args.base_url)[1],
        "thinking_mode": args.thinking,
        "reasoning_effort": args.reasoning_effort,
        "max_attempts": args.max_attempts,
        "round_trips": args.round_trips,
        "completed_round_trips": len(records),
        "requests_per_round_trip": 2,
        "capability_preflight_sha256": _sha256(args.capability_preflight),
        "capability_probe_script_sha256": _sha256(CAPABILITY_PROBE),
        "preflight_observed_response_model": expected_observed_model,
        "observed_response_model": expected_observed_model if passed else "",
        "transport_clean_first_attempt": passed,
        "fallback_used": False,
        "failure": failure,
        "rounds": records,
        "boundary": "Synthetic tool results only; no benchmark Case, real tool, credential, or model free text is recorded.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
