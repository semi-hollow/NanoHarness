from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


OFFICIAL_EVALUATED = {"official_resolved", "official_eval_failed"}
NUMERIC_METRICS = (
    "llm_calls",
    "total_tokens",
    "estimated_cost_usd",
    "llm_latency_ms",
    "tool_calls",
    "failed_tool_calls",
)


# PRIMARY ENTRYPOINT: normalize benchmark artifacts into claim-safe metrics.
def build_benchmark_scorecard(results: dict[str, Any], run_dir: str | Path) -> dict[str, Any]:
    """Build one claim-safe aggregate view from benchmark and usage evidence.

    Benchmark reporting calls this after case evaluation. It separates patch,
    local-validation, and official-evaluation denominators, then returns the
    scorecard consumed by Markdown reports and paired ablations.
    """

    root = Path(run_dir)
    cases = [_normalize_case(item, root) for item in results.get("case_results", []) if isinstance(item, dict)]
    variants = _aggregate_variants(results.get("variant_comparisons") or {})
    observed_models = sorted(
        {
            model
            for case in cases
            for model in case.pop("_observed_models", [])
            if model
        }
    )
    observed_container_image_ids = sorted(
        {
            image_id
            for case in cases
            for image_id in [case.pop("_observed_container_image_id", "")]
            if image_id
        }
    )
    return {
        "schema_version": 1,
        "metadata": {
            "run_id": str(results.get("run_id") or ""),
            "dataset_name": str(results.get("dataset_name") or ""),
            "split": str(results.get("split") or ""),
            "provider": str(results.get("provider") or ""),
            "requested_model": str(results.get("model") or ""),
            "observed_models": observed_models,
            "observed_container_image_ids": observed_container_image_ids,
            "agent_mode": str(results.get("agent_mode") or ""),
            "profile": str(results.get("profile") or ""),
            "max_steps": _int(results.get("max_steps")),
            "max_context_chars": _int(results.get("max_context_chars")),
            "max_revision_rounds": _int(results.get("max_revision_rounds")),
            "tool_routing_mode": str(results.get("tool_routing_mode") or "task-aware"),
            "execution_mode": str(results.get("execution_mode") or "local"),
            "network_policy": str(results.get("network_policy") or "deny"),
            "keep_worktree": bool(results.get("keep_worktree")),
            "container_runtime": str(results.get("container_runtime") or "docker"),
            "container_image": str(results.get("container_image") or "python:3.11-slim"),
            "container_cpus": _float(results.get("container_cpus") or 1.0),
            "container_memory": str(results.get("container_memory") or "1g"),
            "container_pids_limit": _int(results.get("container_pids_limit") or 256),
            "container_read_only": bool(results.get("container_read_only", True)),
        },
        "metrics": _aggregate_cases(cases),
        "cases": cases,
        "variants": variants,
        "claim_boundary": {
            "candidate_patch": "non-empty diff only",
            "local_verified": "all recorded test-oriented validation evidence passed",
            "official_resolved": "official per-case SWE-bench report resolved=true",
        },
    }


def write_benchmark_scorecard(
    results: dict[str, Any],
    run_dir: str | Path,
) -> tuple[Path, Path]:
    """Write scorecard.json and scorecard.md next to benchmark results."""

    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    scorecard = build_benchmark_scorecard(results, root)
    json_path = root / "scorecard.json"
    report_path = root / "scorecard.md"
    json_path.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_benchmark_scorecard(scorecard), encoding="utf-8")
    return json_path, report_path


def load_benchmark_scorecard(run_dir: str | Path) -> dict[str, Any]:
    """Load a scorecard, rebuilding it from results.json when necessary."""

    root = Path(run_dir)
    scorecard_path = root / "scorecard.json"
    if scorecard_path.exists():
        data = json.loads(scorecard_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"invalid scorecard object: {scorecard_path}")
        return data
    results_path = root / "results.json"
    if not results_path.exists():
        raise ValueError(f"benchmark run has no scorecard.json or results.json: {root}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    if not isinstance(results, dict):
        raise ValueError(f"invalid benchmark results object: {results_path}")
    return build_benchmark_scorecard(results, root)


def render_benchmark_scorecard(scorecard: dict[str, Any]) -> str:
    metadata = scorecard.get("metadata") or {}
    metrics = scorecard.get("metrics") or {}
    official_rate = metrics.get("official_resolved_rate")
    official_text = "not available" if official_rate is None else f"{official_rate:.2%}"
    lines = [
        "# Benchmark Evidence Scorecard",
        "",
        "## Run Identity",
        "",
        f"- run_id: `{metadata.get('run_id', '')}`",
        f"- dataset/split: `{metadata.get('dataset_name', '')}` / `{metadata.get('split', '')}`",
        f"- provider/model: `{metadata.get('provider', '')}` / `{metadata.get('requested_model', '') or 'default'}`",
        f"- observed models: `{metadata.get('observed_models', [])}`",
        f"- tool routing: `{metadata.get('tool_routing_mode', '')}`",
        f"- execution/network: `{metadata.get('execution_mode', 'local')}` / "
        f"`{metadata.get('network_policy', 'deny')}`",
        f"- observed container image ids: `{metadata.get('observed_container_image_ids', [])}`",
        "",
        "## Aggregate Metrics",
        "",
        "| Metric | Value | Denominator |",
        "| --- | ---: | ---: |",
        f"| candidate patches | {metrics.get('patch_generated_count', 0)} | {metrics.get('case_count', 0)} |",
        f"| locally verified | {metrics.get('local_verified_count', 0)} | {metrics.get('case_count', 0)} |",
        f"| officially resolved | {metrics.get('official_resolved_count', 0)} | {metrics.get('official_evaluated_count', 0)} |",
        f"| official resolved rate | {official_text} | officially evaluated cases only |",
        f"| total tokens | {metrics.get('total_tokens', 0)} | provider usage |",
        f"| estimated cost USD | {metrics.get('estimated_cost_usd', 0.0):.6f} | provider usage |",
        f"| LLM latency ms | {metrics.get('llm_latency_ms', 0)} | provider calls |",
        f"| failed tool calls | {metrics.get('failed_tool_calls', 0)} | {metrics.get('tool_calls', 0)} tool calls |",
        "",
        "## Evidence Denominators",
        "",
        "- Candidate patch rate measures edit reachability, not correctness.",
        "- Local verification counts only explicit test-oriented validation events; compilation is excluded.",
        "- Official resolved rate uses only cases with explicit resolved/unresolved official reports.",
    ]
    if official_rate is None:
        lines.append("- No official resolved rate is reported because zero cases have official resolved/unresolved evidence.")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| instance | patch | local | official | tokens | cost USD | failed tools | failure class |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for case in scorecard.get("cases", []):
        lines.append(
            "| `{instance_id}` | {patch} | `{local}` | `{official}` | {tokens} | {cost:.6f} | {failed} | `{failure}` |".format(
                instance_id=case.get("instance_id", ""),
                patch="yes" if case.get("patch_generated") else "no",
                local=case.get("local_validation_status", "not_run"),
                official=case.get("official_evaluation_status", "not_evaluated"),
                tokens=case.get("total_tokens", 0),
                cost=float(case.get("estimated_cost_usd") or 0.0),
                failed=case.get("failed_tool_calls", 0),
                failure=case.get("failure_class", "unclassified"),
            )
        )
    if not scorecard.get("cases"):
        lines.append("| none | no | `not_run` | `not_evaluated` | 0 | 0.000000 | 0 | `unclassified` |")
    lines.append("")
    return "\n".join(lines)


def _normalize_case(case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    usage = _load_case_usage(case, run_dir)
    environment = _load_case_environment(case, run_dir)
    probe_value = environment.get("probe")
    probe: dict[str, Any] = probe_value if isinstance(probe_value, dict) else {}
    summary_value = usage.get("summary")
    summary: dict[str, Any] = summary_value if isinstance(summary_value, dict) else {}
    official = str(
        case.get("official_evaluation_status")
        or _official_fallback(str(case.get("evaluation_status") or ""))
        or "not_evaluated"
    )
    local = str(case.get("local_validation_status") or "not_run")
    normalized = {
        "instance_id": str(case.get("instance_id") or ""),
        "status": str(case.get("status") or ""),
        "patch_generated": _int(case.get("patch_chars")) > 0 or bool(case.get("patch_generated")),
        "patch_chars": _int(case.get("patch_chars")),
        "local_validation_status": local,
        "local_verified": local == "passed",
        "official_evaluation_status": official,
        "official_evaluated": official in OFFICIAL_EVALUATED,
        "official_resolved": official == "official_resolved",
        "failure_class": str(case.get("failure_class") or "unclassified"),
        "execution_mode": str(probe.get("mode") or ""),
        "container_image_id": str(probe.get("container_image_id") or ""),
        "llm_calls": _int(summary.get("llm_calls")),
        "total_tokens": _int(summary.get("total_tokens")),
        "estimated_cost_usd": _float(summary.get("estimated_cost_usd")),
        "llm_latency_ms": _int(summary.get("llm_latency_ms")),
        "tool_calls": _int(summary.get("tool_calls")),
        "failed_tool_calls": _int(summary.get("failed_tool_calls")),
        "_observed_models": _observed_models(usage),
        "_observed_container_image_id": str(probe.get("container_image_id") or ""),
    }
    return normalized


def _aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(cases)
    patch_count = sum(bool(case.get("patch_generated")) for case in cases)
    local_count = sum(
        bool(case.get("local_verified")) or case.get("local_validation_status") == "passed"
        for case in cases
    )
    official_evaluated = sum(
        bool(case.get("official_evaluated"))
        or case.get("official_evaluation_status") in OFFICIAL_EVALUATED
        for case in cases
    )
    official_resolved = sum(
        bool(case.get("official_resolved"))
        or case.get("official_evaluation_status") == "official_resolved"
        for case in cases
    )
    metrics: dict[str, Any] = {
        "case_count": case_count,
        "patch_generated_count": patch_count,
        "patch_generated_rate": patch_count / case_count if case_count else None,
        "local_verified_count": local_count,
        "local_verified_rate": local_count / case_count if case_count else None,
        "official_evaluated_count": official_evaluated,
        "official_resolved_count": official_resolved,
        "official_resolved_rate": official_resolved / official_evaluated if official_evaluated else None,
        "failure_classes": dict(sorted(Counter(str(case.get("failure_class") or "unclassified") for case in cases).items())),
        "official_statuses": dict(sorted(Counter(str(case.get("official_evaluation_status") or "not_evaluated") for case in cases).items())),
    }
    for key in NUMERIC_METRICS:
        total = sum(_float(case.get(key)) for case in cases)
        metrics[key] = round(total, 6) if key == "estimated_cost_usd" else int(total)
    return metrics


def _aggregate_variants(comparisons: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for comparison in comparisons.values():
        variants = comparison.get("variants") if isinstance(comparison, dict) else None
        if not isinstance(variants, dict):
            continue
        for name, item in variants.items():
            if isinstance(item, dict):
                rows.setdefault(str(name), []).append(dict(item))
    return {name: _aggregate_cases(items) for name, items in sorted(rows.items())}


def _load_case_usage(case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    candidates = []
    report_value = str(case.get("usage_report_path") or "").strip()
    if report_value:
        report = Path(report_value)
        candidates.append(report.with_name("usage.json") if report.name == "usage_report.md" else report.with_suffix(".json"))
    instance_id = _safe_id(str(case.get("instance_id") or ""))
    candidates.append(run_dir / "cases" / instance_id / "usage.json")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _load_case_environment(case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    candidates = []
    for key in ("patch_path", "trace_path"):
        value = str(case.get(key) or "").strip()
        if not value:
            continue
        artifact = Path(value)
        if not artifact.is_absolute():
            artifact = run_dir / artifact
        candidates.append(artifact.parent / "execution_environment.json")
    instance_id = _safe_id(str(case.get("instance_id") or ""))
    candidates.append(run_dir / "cases" / instance_id / "execution_environment.json")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _observed_models(usage: dict[str, Any]) -> list[str]:
    models = set()
    for step in usage.get("steps", []):
        if not isinstance(step, dict):
            continue
        for call in step.get("llm_calls", []):
            if isinstance(call, dict) and call.get("model"):
                models.add(str(call["model"]))
    return sorted(models)


def _official_fallback(evaluation_status: str) -> str:
    return evaluation_status if evaluation_status.startswith("official_") else ""


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
