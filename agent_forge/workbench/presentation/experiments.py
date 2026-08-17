"""实验资产的通用 Workbench 展示。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable

from agent_forge.workbench.domain import ExperimentBundle

_METRICS = (
    ("llm_calls", "LLM calls"),
    ("total_tokens", "Total tokens"),
    ("tool_calls", "Tool calls"),
    ("search_calls", "Search calls"),
    ("read_file_calls", "Read file"),
    ("failed_tool_calls", "Failed Tool"),
    ("failed_validations", "Failed validation"),
    ("tool_calls_before_first_edit_mean", "首次 Edit 前 Tool"),
    ("first_edit_call_index_mean", "首次 Edit 位置"),
    ("estimated_cost_usd", "Estimated cost"),
)

_OUTCOMES = {
    "resolved": "Resolved",
    "unresolved": "Unresolved",
    "official_resolved": "Official Resolved",
    "official_unresolved": "Official Unresolved",
    "agent_terminal_empty_patch": "Agent terminal Empty Patch",
}

_TRANSITIONS = {
    "unresolved_to_resolved": "Gain：Unresolved → Resolved",
    "resolved_to_unresolved": "Regression：Resolved → Unresolved",
    "resolved_to_resolved": "保持 Resolved",
    "unresolved_to_unresolved": "保持 Unresolved",
}


def render_experiment_bundle(bundle: ExperimentBundle) -> str:
    """按 manifest 指定的通用 item 渲染，不给单个实验写专属 HTML。"""

    source = bundle.source
    if source.item_kind == "variables":
        content = _render_variables(bundle)
    elif source.item_kind == "results":
        content = _render_results(bundle)
    elif source.item_kind == "evidence":
        content = _render_evidence(bundle)
    elif source.item_kind == "case":
        content = _render_case(bundle)
    else:
        content = _render_overview(bundle)
    decision = _mapping(bundle.manifest.get("decision"))
    return (
        "<div class='experiment-workspace'>"
        "<section class='source-identity experiment-identity'>"
        "<div><span>EXPERIMENT EVIDENCE</span>"
        f"<h2>{_escape(source.title)}</h2>"
        f"<p>{_escape(source.description)}</p></div>"
        f"{_badge(source.decision, _decision_tone(source.decision))}"
        "<dl>"
        f"<div><dt>实验族</dt><dd>{_escape(source.family_title)}</dd></div>"
        f"<div><dt>比较 / 测量</dt><dd>{_escape(source.comparison_title)}</dd></div>"
        f"<div><dt>当前分析项</dt><dd>{_escape(source.item_title)}</dd></div>"
        f"<div><dt>实验 ID</dt><dd class='mono'>{_escape(source.experiment_id)}</dd></div>"
        f"<div><dt>决策门</dt><dd>{_escape(decision.get('gate') or '-')}</dd></div>"
        f"<div><dt>机器状态</dt><dd>{_escape(source.status)}</dd></div>"
        "</dl></section>"
        f"{content}</div>"
    )


def _render_overview(bundle: ExperimentBundle) -> str:
    manifest = bundle.manifest
    decision = _mapping(manifest.get("decision"))
    summary_cards = _headline_cards(bundle)
    controls = _string_list(manifest.get("fixed_controls"))
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>EXPERIMENT SUMMARY</span><h2>实验概览</h2>"
        "</div>"
        f"{_badge(decision.get('status') or '-', _decision_tone(decision.get('status')))}</div>"
        f"<p class='help strong'>{_escape(manifest.get('question') or '')}</p>"
        + _metric_cards(summary_cards)
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>假设与单一主变量</h3><span>先读设计，再读数字</span></div>"
        "<div class='answer-strip experiment-premise'>"
        f"<div><b>假设</b><span>{_escape(manifest.get('hypothesis') or '-')}</span></div>"
        f"<div><b>主变量</b><span>{_escape(manifest.get('primary_variable') or '-')}</span></div>"
        f"<div><b>最终判断</b><span>{_escape(decision.get('summary') or '-')}</span></div>"
        "</div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>固定控制项</h3><span>没有变化的条件</span></div>"
        f"{_list_html(controls, 'next-actions')}"
        "</section>" + _evidence_layers() + _claim_boundary(bundle) + "</div>"
    )


def _render_variables(bundle: ExperimentBundle) -> str:
    manifest = bundle.manifest
    rows = []
    changes = manifest.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            rows.append(
                "<tr>"
                f"<td><strong>{_escape(change.get('name') or '-')}</strong></td>"
                f"<td>{_escape(change.get('baseline') or '-')}</td>"
                f"<td>{_escape(change.get('candidate') or '-')}</td>"
                f"<td><code>{_escape(change.get('implementation') or '-')}</code>"
                f"<span class='quality-cell-detail'>{_escape(change.get('assessment') or '')}</span></td>"
                "</tr>"
            )
    experiment = _mapping(bundle.plan.get("experiment"))
    deferred = _string_list(experiment.get("deferred"))
    identity = _mapping(bundle.result.get("identity"))
    provenance = _experiment_provenance(bundle)
    version_facts = [
        (
            "Treatment commit",
            identity.get("treatment_commit") or provenance.get("treatment_commit"),
        ),
        (
            "Frozen run source",
            identity.get("run_source_commit")
            or provenance.get("run_source_commit")
            or _mapping(bundle.result.get("integrity")).get("source_revision"),
        ),
        (
            "Rollback commit",
            identity.get("rollback_commit") or provenance.get("rollback_commit"),
        ),
    ]
    version_rows = "".join(
        "<div>"
        f"<b>{_escape(label)}</b><span class='mono'>{_escape(value or 'not applicable')}</span>"
        "</div>"
        for label, value in version_facts
    )
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>CONTROLLED VARIABLE</span><h2>变量与实现</h2>"
        "</div></div>"
        "<p class='help strong'>表格中的 Candidate 是本轮唯一允许变化的 Tool / ACI surface；"
        "固定条件和 defer 项不应被事后混入因果解释。</p>"
        "<section class='evidence-section'><table class='experiment-variable-table'><thead><tr>"
        "<th>能力</th><th>Baseline</th><th>Candidate / 测量配置</th><th>实现与判断</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>明确不进入本轮的变量</h3><span>防止范围膨胀</span></div>"
        f"{_list_html(deferred or ['本轮没有额外 Treatment 变量'], 'next-actions')}"
        "</section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>代码版本定位</h3><span>当前 stable 看不到已回滚 Treatment 时，从 commit 复核</span></div>"
        f"<div class='answer-strip experiment-version-facts'>{version_rows}</div>"
        "</section></div>"
    )


def _render_results(bundle: ExperimentBundle) -> str:
    if bundle.source.experiment_kind == "paired_ab":
        return _render_paired_results(bundle)
    return _render_measurement_results(bundle)


def _render_paired_results(bundle: ExperimentBundle) -> str:
    result = bundle.result
    comparison = _mapping(bundle.manifest.get("comparison"))
    baseline_id = str(comparison.get("baseline_id") or "r0")
    candidate_id = str(comparison.get("candidate_id") or "")
    baseline = _mapping(result.get(baseline_id))
    candidate = _mapping(result.get(candidate_id))
    paired = _mapping(result.get("paired"))
    transition_counts = _mapping(paired.get("transition_counts"))
    rows = []
    base_metrics = _mapping(baseline.get("metrics"))
    candidate_metrics = _mapping(candidate.get("metrics"))
    for key, label in _METRICS:
        if key not in base_metrics and key not in candidate_metrics:
            continue
        base_value = base_metrics.get(key)
        candidate_value = candidate_metrics.get(key)
        rows.append(
            "<tr>"
            f"<td>{_escape(label)}</td><td>{_format_metric(key, base_value)}</td>"
            f"<td>{_format_metric(key, candidate_value)}</td>"
            f"<td>{_format_delta(base_value, candidate_value, key)}</td></tr>"
        )
    transition_rows = []
    for key in (
        "unresolved_to_resolved",
        "resolved_to_unresolved",
        "resolved_to_resolved",
        "unresolved_to_unresolved",
    ):
        transition_rows.append(
            "<tr>"
            f"<td>{_escape(_TRANSITIONS[key])}</td>"
            f"<td>{_escape(transition_counts.get(key, 0))}</td></tr>"
        )
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>PAIRED RESULT</span><h2>结果对比</h2>"
        "</div>"
        f"{_badge(paired.get('decision') or '-', _decision_tone(paired.get('decision')))}</div>"
        + _metric_cards(
            [
                (
                    "Baseline",
                    f"{baseline.get('official_resolved', 0)}/{baseline.get('planned', 0)}",
                    str(comparison.get("baseline_label") or baseline_id),
                ),
                (
                    "Candidate",
                    f"{candidate.get('official_resolved', 0)}/{candidate.get('planned', 0)}",
                    str(comparison.get("candidate_label") or candidate_id),
                ),
                (
                    "Net delta",
                    _signed(paired.get("net_resolved_delta")),
                    "official resolved",
                ),
                (
                    "Percentage point",
                    _signed(paired.get("percentage_point_delta"), suffix=" pp"),
                    "同一 20 Case",
                ),
                (
                    "McNemar p",
                    _number(paired.get("mcnemar_exact_two_sided_p")),
                    "exact two-sided",
                ),
                ("Decision", str(paired.get("decision") or "-"), "按预注册 gate"),
            ]
        )
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>过程指标</h3><span>机器结果直接投影</span></div>"
        "<table><thead><tr><th>指标</th>"
        f"<th>{_escape(comparison.get('baseline_label') or baseline_id)}</th>"
        f"<th>{_escape(comparison.get('candidate_label') or candidate_id)}</th>"
        f"<th>变化</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>逐 Case 转移</h3><span>选择具体 Case 可继续下钻</span></div>"
        f"<table class='compact-table'><thead><tr><th>转移</th><th>数量</th></tr></thead>"
        f"<tbody>{''.join(transition_rows)}</tbody></table>"
        f"{_transition_case_list(paired)}</section>"
        + _claim_boundary(bundle)
        + "</div>"
    )


def _render_measurement_results(bundle: ExperimentBundle) -> str:
    result = bundle.result
    infra = _mapping(result.get("infrastructure_invalid"))
    resources = _mapping(
        _mapping(result.get("resources")).get("selected_50_trajectories")
    )
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>ABSOLUTE MEASUREMENT</span><h2>Mini-50 结果</h2>"
        "</div>"
        f"{_badge('published', 'ok')}</div>"
        + _metric_cards(
            [
                (
                    "Official resolved",
                    f"{result.get('official_resolved', 0)}/{result.get('planned', 0)}",
                    str(result.get("headline") or ""),
                ),
                (
                    "Official unresolved",
                    str(result.get("official_unresolved", 0)),
                    "official evaluated",
                ),
                (
                    "Agent Empty Patch",
                    str(result.get("agent_terminal_empty_patch", 0)),
                    "计入 planned 分母",
                ),
                (
                    "Terminal accounted",
                    f"{result.get('terminal_accounted', 0)}/{result.get('planned', 0)}",
                    "完整分母",
                ),
                (
                    "Total tokens",
                    _format_integer(resources.get("total_tokens")),
                    "selected 50 trajectories",
                ),
                (
                    "Estimated cost",
                    _format_cost(resources.get("estimated_cost_usd")),
                    "selected 50 trajectories",
                ),
            ]
        )
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>发布门</h3><span>全部条件成立才显示 X/50</span></div>"
        "<div class='answer-strip'>"
        f"<div><b>完整性</b><span>planned={_escape(result.get('planned'))} · completed={_escape(result.get('completed'))} · terminal={_escape(result.get('terminal_accounted'))}</span></div>"
        f"<div><b>基础设施</b><span>provider={_escape(infra.get('provider', 0))} · runtime={_escape(infra.get('runtime', 0))} · evaluator={_escape(infra.get('evaluator', 0))} · external={_escape(infra.get('external_interruption', 0))}</span></div>"
        f"<div><b>Patch 字节链</b><span>{_escape(_mapping(result.get('integrity')).get('nonempty_patch_byte_chains_verified', 0))} verified · {_escape(_mapping(result.get('integrity')).get('nonempty_patch_byte_chain_mismatches', 0))} mismatch</span></div>"
        "</div></section>"
        + _render_empty_patch_summary(bundle)
        + _claim_boundary(bundle)
        + "</div>"
    )


def _render_case(bundle: ExperimentBundle) -> str:
    if bundle.source.experiment_kind == "paired_ab":
        return _render_paired_case(bundle)
    return _render_measurement_case(bundle)


def _render_paired_case(bundle: ExperimentBundle) -> str:
    case_id = bundle.source.case_id
    comparison = _mapping(bundle.manifest.get("comparison"))
    baseline_id = str(comparison.get("baseline_id") or "r0")
    candidate_id = str(comparison.get("candidate_id") or "")
    baseline = _mapping(_mapping(bundle.result.get(baseline_id)).get("cases")).get(
        case_id
    )
    candidate = _mapping(_mapping(bundle.result.get(candidate_id)).get("cases")).get(
        case_id
    )
    baseline_case = _mapping(baseline)
    candidate_case = _mapping(candidate)
    transition = next(
        (
            item
            for item in _list_of_mappings(
                _mapping(bundle.result.get("paired")).get("transitions")
            )
            if item.get("instance_id") == case_id
        ),
        {},
    )
    metric_rows = []
    for key, label in _METRICS[:8]:
        base_value = _mapping(baseline_case.get("metrics")).get(key)
        candidate_value = _mapping(candidate_case.get("metrics")).get(key)
        if base_value is None and candidate_value is None:
            continue
        metric_rows.append(
            "<tr>"
            f"<td>{_escape(label)}</td><td>{_format_metric(key, base_value)}</td>"
            f"<td>{_format_metric(key, candidate_value)}</td>"
            f"<td>{_format_delta(base_value, candidate_value, key)}</td></tr>"
        )
    run_rows = _case_run_rows(bundle, case_id)
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>CASE TRANSITION</span>"
        f"<h2>{_escape(case_id)}</h2></div>"
        f"{_badge(_TRANSITIONS.get(str(transition.get('transition')), transition.get('transition') or '-'), _transition_tone(str(transition.get('transition') or '')))}</div>"
        + _metric_cards(
            [
                (
                    str(comparison.get("baseline_label") or baseline_id),
                    _outcome(baseline_case),
                    str(baseline_case.get("task_status") or "-"),
                ),
                (
                    str(comparison.get("candidate_label") or candidate_id),
                    _outcome(candidate_case),
                    str(candidate_case.get("task_status") or "-"),
                ),
                ("Subset", str(transition.get("subset") or "-"), "development cohort"),
                (
                    "Baseline Patch",
                    _short_sha(baseline_case.get("patch_sha256")),
                    f"{baseline_case.get('patch_bytes', 0)} bytes",
                ),
                (
                    "Candidate Patch",
                    _short_sha(candidate_case.get("patch_sha256")),
                    f"{candidate_case.get('patch_bytes', 0)} bytes",
                ),
                (
                    "Local validation",
                    f"{baseline_case.get('local_validation_status', '-')} → {candidate_case.get('local_validation_status', '-')}",
                    "不是 official verdict",
                ),
            ]
        )
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>同 Case 过程对比</h3><span>不把相关性写成单组件因果</span></div>"
        "<table><thead><tr><th>指标</th>"
        f"<th>{_escape(comparison.get('baseline_label') or baseline_id)}</th>"
        f"<th>{_escape(comparison.get('candidate_label') or candidate_id)}</th><th>变化</th>"
        f"</tr></thead><tbody>{''.join(metric_rows)}</tbody></table></section>"
        "<section class='evidence-section'><div class='section-title'><h3>停止与代码产物</h3>"
        "<span>两条 trajectory 独立留证</span></div>"
        "<div class='answer-strip'>"
        f"<div><b>Baseline stop</b><span>{_escape(baseline_case.get('stop_reason') or '-')}</span></div>"
        f"<div><b>Candidate stop</b><span>{_escape(candidate_case.get('stop_reason') or '-')}</span></div>"
        f"<div><b>Changed files</b><span>R0: {_escape(', '.join(_string_list(baseline_case.get('files_changed'))) or '-')}<br>Candidate: {_escape(', '.join(_string_list(candidate_case.get('files_changed'))) or '-')}</span></div>"
        "</div></section>"
        f"{run_rows}"
        "<p class='boundary-note'><strong>阅读边界：</strong>本页展示 paired outcome 与显式 Trace/Usage 指标；"
        "它可以定位关联变化，不能把 Bundle 结果归因给某一个 Tool。</p></div>"
    )


def _render_measurement_case(bundle: ExperimentBundle) -> str:
    case_id = bundle.source.case_id
    outcome = "unknown"
    case_groups = bundle.result.get("case_ids")
    if isinstance(case_groups, dict):
        for name, case_ids in case_groups.items():
            if isinstance(case_ids, list) and case_id in case_ids:
                outcome = str(name)
                break
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>MEASUREMENT CASE</span>"
        f"<h2>{_escape(case_id)}</h2></div>"
        f"{_badge(_OUTCOMES.get(outcome, outcome), _outcome_tone(outcome))}</div>"
        "<section class='evidence-section'><div class='answer-strip'>"
        f"<div><b>最终分类</b><span>{_escape(_OUTCOMES.get(outcome, outcome))}</span></div>"
        "<div><b>分母口径</b><span>该 Case 恰好贡献 Mini-50 planned 分母中的 1 个终态。</span></div>"
        "<div><b>行为诊断</b><span>切换到“运行证据 → Mini-50 → 当前 Case”读取 Tool 参数、Observation、停止原因与原始 Trace。</span></div>"
        "</div></section>"
        f"{_render_empty_patch_case(bundle, case_id)}"
        f"{_case_run_rows(bundle, case_id)}"
        "<p class='boundary-note'><strong>证据边界：</strong>实验视图负责结果分类和完整分母；"
        "Runtime 视图负责逐步行为诊断，两者不复制同一份 Trace。</p></div>"
    )


def _render_empty_patch_summary(bundle: ExperimentBundle) -> str:
    review = _failure_review(bundle)
    cases = _list_of_mappings(review.get("cases"))
    if not cases:
        return ""
    summary = _mapping(review.get("summary"))
    rows = "".join(
        "<tr>"
        f"<td><strong>{_escape(item.get('case_id') or '-')}</strong></td>"
        f"<td>{_escape(item.get('terminal_reason') or '-')}</td>"
        f"<td>{_escape(_mapping(item.get('root_cause_classification')).get('primary') or '-')}</td>"
        f"<td>{_escape('none' if item.get('first_successful_write') is None else item.get('first_successful_write'))}</td>"
        "</tr>"
        for item in cases
    )
    return (
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Empty Patch Failure Review</h3>"
        "<span>sanitized derived evidence · exactly 6 cases</span></div>"
        "<div class='answer-strip'>"
        f"<div><b>Failure fuse</b><span>{_escape(summary.get('consecutive_tool_failure_fuse', 0))} cases</span></div>"
        f"<div><b>Run timeout</b><span>{_escape(summary.get('run_timeout', 0))} case</span></div>"
        f"<div><b>First write</b><span>{_escape(summary.get('successful_write', 0))} successful</span></div>"
        f"<div><b>Identical loop</b><span>{_escape(summary.get('repeated_identical_call_sequence', 0))} detected</span></div>"
        "</div><div class='review-table-scroll'><table><thead><tr>"
        "<th>Case</th><th>Terminal</th><th>Evidence-backed RCA</th><th>First write</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
        "<p class='boundary-note'><strong>xarray-3151：</strong>5 次已执行的聚焦 read/search 后，"
        "第 4 次模型请求在两次 attempt 中持续约 2 小时 47 分；它不是 Context 或搜索失控。</p>"
        "</section>"
    )


def _render_empty_patch_case(bundle: ExperimentBundle, case_id: str) -> str:
    review = _failure_review(bundle)
    case = next(
        (
            item
            for item in _list_of_mappings(review.get("cases"))
            if item.get("case_id") == case_id
        ),
        None,
    )
    if case is None:
        return ""
    trigger = _mapping(case.get("first_failure_trigger"))
    root_cause = _mapping(case.get("root_cause_classification"))
    progress = _mapping(case.get("last_effective_progress"))
    recovery = "".join(
        "<li>"
        f"<b>Step {_escape(item.get('step') or '-')}</b> · "
        f"{_escape(item.get('action') or '-')} → {_escape(item.get('outcome') or '-')}"
        "</li>"
        for item in _list_of_mappings(case.get("recovery_actions"))
    ) or "<li>No executed recovery action before timeout.</li>"
    collisions = " · ".join(_string_list(case.get("policy_or_environment_collisions")))
    first_write = (
        "none"
        if case.get("first_successful_write") is None
        else _escape(case.get("first_successful_write"))
    )
    return (
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Empty Patch Failure Review</h3><span>frozen sanitized projection</span></div>"
        "<div class='answer-strip'>"
        f"<div><b>Initial trigger</b><span>Step {_escape(trigger.get('step') or '-')} · {_escape(trigger.get('kind') or '-')}<br>{_escape(trigger.get('evidence') or '-')}</span></div>"
        f"<div><b>Terminal</b><span>{_escape(case.get('terminal_reason') or '-')}</span></div>"
        f"<div><b>First write</b><span>{first_write}</span></div>"
        f"<div><b>Root cause</b><span>{_escape(root_cause.get('primary') or '-')}</span></div>"
        "</div>"
        "<h4>Recovery path</h4>"
        f"<ul class='next-actions'>{recovery}</ul>"
        "<div class='answer-strip'>"
        f"<div><b>Policy / environment collision</b><span>{_escape(collisions or 'none observed')}</span></div>"
        f"<div><b>Last effective progress</b><span>Step {_escape(progress.get('step') or '-')} · {_escape(progress.get('evidence') or '-')}</span></div>"
        f"<div><b>Next design lever</b><span>{_escape(case.get('potential_runtime_improvement') or '-')}</span></div>"
        "</div></section>"
    )


def _failure_review(bundle: ExperimentBundle) -> dict[str, Any]:
    return next(
        (
            _read_json(path)
            for role, path in bundle.artifacts
            if role == "failure_review"
        ),
        {},
    )


def _render_evidence(bundle: ExperimentBundle) -> str:
    experiment_provenance = _experiment_provenance(bundle)
    rows = []
    for artifact in _list_of_mappings(experiment_provenance.get("artifacts")):
        producer = str(artifact.get("producer") or "unknown")
        rows.append(
            "<tr>"
            f"<td>{_producer_badge(producer)}</td>"
            f"<td>{_escape(artifact.get('role') or '-')}</td>"
            f"<td><code>{_escape(artifact.get('locator') or '-')}</code></td></tr>"
        )
    direct_paths = "".join(
        "<tr>"
        f"<td>{_escape(role)}</td><td colspan='2'><code>{_escape(path)}</code></td></tr>"
        for role, path in bundle.artifacts
    )
    identity = _mapping(bundle.result.get("identity")) or _mapping(
        bundle.result.get("integrity")
    )
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>PROVENANCE</span><h2>证据与边界</h2>"
        "</div></div>"
        + _evidence_layers()
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>产物来源</h3><span>原始测量、确定性派生、人工审阅不混为一层</span></div>"
        "<table><thead><tr><th>Producer</th><th>Role</th><th>Locator</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>当前版本化入口</h3><span>可直接从项目树定位</span></div>"
        "<table><thead><tr><th>Role</th><th colspan='2'>Path</th></tr></thead>"
        f"<tbody>{direct_paths}</tbody></table></section>"
        "<section class='evidence-section'><details class='provenance'><summary>查看机器身份字段</summary>"
        f"<pre class='raw-text'>{_escape(json.dumps(identity, ensure_ascii=False, indent=2))}</pre>"
        "</details></section>" + _claim_boundary(bundle) + "</div>"
    )


def _headline_cards(bundle: ExperimentBundle) -> list[tuple[str, str, str]]:
    if bundle.source.experiment_kind == "paired_ab":
        comparison = _mapping(bundle.manifest.get("comparison"))
        baseline_id = str(comparison.get("baseline_id") or "r0")
        candidate_id = str(comparison.get("candidate_id") or "")
        baseline = _mapping(bundle.result.get(baseline_id))
        candidate = _mapping(bundle.result.get(candidate_id))
        paired = _mapping(bundle.result.get("paired"))
        return [
            (
                str(comparison.get("baseline_label") or baseline_id),
                f"{baseline.get('official_resolved', 0)}/{baseline.get('planned', 0)}",
                "official resolved",
            ),
            (
                str(comparison.get("candidate_label") or candidate_id),
                f"{candidate.get('official_resolved', 0)}/{candidate.get('planned', 0)}",
                "official resolved",
            ),
            ("Net delta", _signed(paired.get("net_resolved_delta")), "paired Case"),
            (
                "Gain / Regression",
                f"{_mapping(paired.get('transition_counts')).get('unresolved_to_resolved', 0)} / {_mapping(paired.get('transition_counts')).get('resolved_to_unresolved', 0)}",
                "outcome transitions",
            ),
            ("Decision", str(paired.get("decision") or "-"), "pre-registered gate"),
            (
                "Denominator",
                f"{baseline.get('terminal', 0)}/{baseline.get('planned', 0)} + {candidate.get('terminal', 0)}/{candidate.get('planned', 0)}",
                "terminal trajectories",
            ),
        ]
    result = bundle.result
    return [
        ("Headline", str(result.get("headline") or "-"), "fixed Mini-50"),
        ("Resolved", str(result.get("official_resolved") or 0), "official evaluator"),
        (
            "Unresolved",
            str(result.get("official_unresolved") or 0),
            "official evaluator",
        ),
        (
            "Empty Patch",
            str(result.get("agent_terminal_empty_patch") or 0),
            "Agent terminal",
        ),
        (
            "Terminal",
            f"{result.get('terminal_accounted', 0)}/{result.get('planned', 0)}",
            "complete denominator",
        ),
        ("Status", str(result.get("status") or "-"), "publish gate"),
    ]


def _case_run_rows(bundle: ExperimentBundle, case_id: str) -> str:
    rows = []
    for role, path in bundle.artifacts:
        if not role.startswith("execution_"):
            continue
        payload = _read_json(path)
        run_dirs = _find_case_run_dirs(payload, case_id)
        if not run_dirs:
            raw_root = payload.get("raw_evidence_root")
            if isinstance(raw_root, str) and raw_root:
                run_dirs = [raw_root]
        for run_dir in run_dirs:
            rows.append(
                "<tr>"
                f"<td>{_escape(path.name)}</td><td><code>{_escape(run_dir)}</code></td></tr>"
            )
    if not rows:
        return ""
    return (
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>原始运行定位</h3><span>切换 Runtime 视图读取具体 Trace</span></div>"
        "<table><thead><tr><th>Index</th><th>Run directory</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _find_case_run_dirs(value: object, case_id: str) -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        direct_match = value.get("instance_id") == case_id
        case_ids = value.get("case_ids")
        grouped_match = isinstance(case_ids, list) and case_id in case_ids
        run_dir = value.get("run_dir")
        if (direct_match or grouped_match) and isinstance(run_dir, str):
            matches.append(run_dir)
        for child in value.values():
            matches.extend(_find_case_run_dirs(child, case_id))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_find_case_run_dirs(child, case_id))
    return list(dict.fromkeys(matches))


def _transition_case_list(paired: dict[str, Any]) -> str:
    entries = []
    for transition in _list_of_mappings(paired.get("transitions")):
        name = str(transition.get("transition") or "")
        if name not in {"unresolved_to_resolved", "resolved_to_unresolved"}:
            continue
        entries.append(
            "<div>"
            f"<b>{_escape(_TRANSITIONS.get(name, name))}</b>"
            f"<span>{_escape(transition.get('instance_id') or '-')} · {_escape(transition.get('subset') or '-')}</span>"
            "</div>"
        )
    if not entries:
        return ""
    return (
        "<div class='answer-strip experiment-transitions'>"
        + "".join(entries)
        + "</div>"
    )


def _evidence_layers() -> str:
    return (
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>三层证据</h3><span>层级越高，解释成分越多</span></div>"
        "<div class='answer-strip evidence-layer-strip'>"
        "<div><b>01 原始测量</b><span>Trace、Usage、candidate Patch、official evaluator 输出；由运行流水线直接产生。</span></div>"
        "<div><b>02 确定性派生</b><span>result.json 与 execution index；只做计数、配对、哈希和分类。</span></div>"
        "<div><b>03 审阅解释</b><span>README / report；解释为何接受、拒绝以及哪些结论不能外推。</span></div>"
        "</div></section>"
    )


def _claim_boundary(bundle: ExperimentBundle) -> str:
    claims = _string_list(bundle.manifest.get("boundaries"))
    if not claims:
        claims = _string_list(bundle.result.get("claim_limits"))
    if not claims:
        claims = _string_list(bundle.plan.get("claim_limits"))
    if not claims:
        boundary = bundle.result.get("claim_boundary")
        claims = [str(boundary)] if boundary else []
    if not claims:
        return ""
    return (
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>结论边界</h3><span>这些限制与 headline 同等重要</span></div>"
        f"{_list_html(claims, 'next-actions')}</section>"
    )


def _metric_cards(items: Iterable[tuple[str, str, str]]) -> str:
    return (
        "<div class='metric-grid'>"
        + "".join(
            "<div class='metric'><div class='metric-label'>"
            f"{_escape(label)}</div><div class='metric-value'>{_escape(value)}</div>"
            f"<div class='metric-help'>{_escape(note)}</div></div>"
            for label, value, note in items
        )
        + "</div>"
    )


def _producer_badge(producer: str) -> str:
    labels = {
        "pipeline_direct": ("原始测量", "ok"),
        "pipeline_index": ("运行索引", "neutral"),
        "deterministic_postprocessor": ("确定性派生", "warn"),
        "codex_assisted_implementation": ("辅助实现", "neutral"),
        "codex_assisted_human_reviewed": ("审阅解释", "neutral"),
    }
    label, tone = labels.get(producer, (producer, "neutral"))
    return _badge(label, tone)


def _experiment_provenance(bundle: ExperimentBundle) -> dict[str, Any]:
    return next(
        (
            item
            for item in _list_of_mappings(bundle.provenance.get("experiments"))
            if item.get("experiment_id") == bundle.source.experiment_id
        ),
        {},
    )


def _badge(value: object, tone: str) -> str:
    return f"<span class='badge {tone}'>{_escape(value)}</span>"


def _list_html(items: Iterable[str], css_class: str) -> str:
    return (
        f"<ul class='{css_class}'>"
        + "".join(f"<li>{_escape(item)}</li>" for item in items)
        + "</ul>"
    )


def _format_metric(key: str, value: object) -> str:
    if value is None:
        return "-"
    if key == "estimated_cost_usd":
        return _format_cost(value)
    if isinstance(value, int):
        return _format_integer(value)
    if isinstance(value, float):
        return f"{value:,.2f}"
    return _escape(value)


def _format_delta(baseline: object, candidate: object, key: str) -> str:
    if not isinstance(baseline, (int, float)) or isinstance(baseline, bool):
        return "-"
    if not isinstance(candidate, (int, float)) or isinstance(candidate, bool):
        return "-"
    delta = candidate - baseline
    percent = "" if baseline == 0 else f" ({delta / baseline * 100:+.1f}%)"
    prefix = "$" if key == "estimated_cost_usd" else ""
    return (
        f"{prefix}{delta:+,.3f}{percent}"
        if isinstance(delta, float)
        else f"{delta:+,}{percent}"
    )


def _format_integer(value: object) -> str:
    return (
        f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else "-"
    )


def _format_cost(value: object) -> str:
    return (
        f"${value:,.4f}"
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else "-"
    )


def _number(value: object) -> str:
    return (
        f"{value:g}"
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else "-"
    )


def _signed(value: object, *, suffix: str = "") -> str:
    return (
        f"{value:+g}{suffix}"
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else "-"
    )


def _outcome(case: dict[str, Any]) -> str:
    value = str(
        case.get("outcome") or ("resolved" if case.get("resolved") else "unresolved")
    )
    return _OUTCOMES.get(value, value)


def _short_sha(value: object) -> str:
    text = str(value or "")
    return f"{text[:10]}…" if len(text) > 10 else text or "-"


def _decision_tone(value: object) -> str:
    normalized = str(value or "").lower()
    if normalized in {"published", "accepted", "pass", "passed"}:
        return "ok"
    if normalized in {"reject", "rejected", "failed", "invalid"}:
        return "bad"
    return "warn"


def _transition_tone(value: str) -> str:
    if value == "unresolved_to_resolved":
        return "ok"
    if value == "resolved_to_unresolved":
        return "bad"
    return "neutral"


def _outcome_tone(value: str) -> str:
    if value == "official_resolved":
        return "ok"
    if value in {"official_unresolved", "agent_terminal_empty_patch"}:
        return "bad"
    return "neutral"


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_mappings(value: object) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _string_list(value: object) -> list[str]:
    return (
        [str(item) for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)
