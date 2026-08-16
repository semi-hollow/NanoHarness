"""三条 Evidence Review 主路径的紧凑 HTML 投影。"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote

from agent_forge.workbench.application.review_projection import (
    REVIEW_MANIFEST,
    Lab1Review,
    Lab2Review,
    Mini50Review,
    ReviewContract,
    build_lab1_review,
    build_lab2_review,
    build_mini50_review,
)
from agent_forge.workbench.domain import EvidenceSource


ARCHITECTURE_URL = (
    "https://github.com/semi-hollow/NanoHarness/blob/master/docs/"
    "%E6%9E%B6%E6%9E%84%E5%AF%BC%E8%A7%88.md"
)


def render_review_overview(
    project_dir: Path,
    source: EvidenceSource,
    sources: tuple[EvidenceSource, ...],
) -> str | None:
    """为三个 canonical overview 返回审阅页；其余来源继续走通用视图。"""

    if not (project_dir / REVIEW_MANIFEST).is_file():
        return None
    if source.item_key != "overview":
        if source.category_key == "evaluation":
            return _render_case_anatomy(source)
        return None
    if source.category_key == "governed":
        return _render_lab1(build_lab1_review(project_dir, source))
    if source.category_key == "orchestration":
        return _render_lab2(build_lab2_review(project_dir, source))
    if source.category_key == "evaluation":
        return _render_mini50(build_mini50_review(project_dir, source, sources))
    return None


def _render_contract_cards(contract: ReviewContract, observed: str) -> str:
    cards = (
        ("QUESTION", "DESIGN CONTRACT", contract.question),
        ("MECHANISM", "DESIGN CONTRACT", contract.mechanism),
        ("EVIDENCE / ACTUAL RESULT", "OBSERVED ARTIFACT", observed),
        ("BOUNDARY", "DESIGN CONTRACT", contract.boundary),
    )
    return "<div class='review-card-grid'>" + "".join(
        "<article class='review-card'>"
        f"<span>{_escape(label)}</span><small>{_escape(source_kind)}</small>"
        f"<p>{_escape(value)}</p></article>"
        for label, source_kind, value in cards
    ) + "</div>"


def _architecture_link(contract: ReviewContract) -> str:
    anchor = quote(contract.architecture_anchor, safe="#-")
    return (
        "<a class='review-link' target='_blank' rel='noreferrer' "
        f"href='{ARCHITECTURE_URL}{anchor}'>打开对应架构章节 ↗</a>"
    )


def _render_lab1(review: Lab1Review) -> str:
    state_cards: list[str] = []
    for index, state in enumerate(review.state_sequence, start=1):
        state_cards.append(
            "<div class='review-flow-step'>"
            f"<small>RUN {index}</small><strong>{_escape(state.upper())}</strong>"
            f"<span>{'OBSERVED ARTIFACT'}</span></div>"
        )
        if index < len(review.state_sequence):
            state_cards.append(
                "<div class='review-boundary'>Operator Boundary<br>persist → resume</div>"
            )
    authority_rows = "".join(
        "<tr>"
        f"<td><b>{_escape(item.owner)}</b></td><td>{_escape(item.question)}</td>"
        f"<td>{_status(item.status)}</td><td>{_escape(item.value)}</td>"
        f"<td><code>{_short_path(item.path)}</code></td></tr>"
        for item in review.authorities
    )
    invariant_cards = "".join(
        "<article class='invariant-card'>"
        f"{_status('PASS' if item.observed else 'NOT OBSERVED')}"
        f"<strong>{_escape(item.statement)}</strong><p>{_escape(item.evidence)}</p>"
        "</article>"
        for item in review.invariants
    )
    revision = review.evidence_revision[:12] if review.evidence_revision else "未记录"
    return (
        "<div class='evidence review-overview'>"
        "<div class='view-heading'><div><span class='view-kicker'>RUNTIME CONTROL</span>"
        f"<h2>{_escape(review.contract.title)}</h2></div>{_status(review.status)}</div>"
        + _render_contract_cards(review.contract, review.observed_result)
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>Durable Control State Machine</h3><span>只显示本次 Run 真实状态</span></div>"
        f"<div class='review-flow'>{''.join(state_cards)}</div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Authority Map</h3><span>五个 Store 回答五个不同问题</span></div>"
        "<div class='review-table-scroll'><table><thead><tr><th>Durable Store</th>"
        "<th>回答的问题</th><th>状态</th><th>当前值</th><th>权威文件</th></tr></thead>"
        f"<tbody>{authority_rows}</tbody></table></div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Control Invariants</h3><span>没有真实事件顺序就不显示 PASS</span></div>"
        f"<div class='invariant-grid'>{invariant_cards}</div></section>"
        "<div class='review-footer'>"
        f"<span>Evidence revision <code>{_escape(revision)}</code></span>"
        + _architecture_link(review.contract)
        + "</div></div>"
    )


def _render_lab2(review: Lab2Review) -> str:
    task_rows = "".join(
        "<tr>"
        f"<td><b>{_escape(task.task_id)}</b></td>"
        f"<td>{_escape(', '.join(task.depends_on) or '—')}</td>"
        f"<td>{_escape(', '.join(task.write_scope) or '∅')}</td>"
        f"<td>{_escape(', '.join(task.touched_files) or '∅')}</td>"
        f"<td>{_status(task.status)}</td></tr>"
        for task in review.tasks
    )
    batch_cards = "".join(
        "<div class='batch-card'>"
        f"<small>BATCH {index}</small><strong>{_escape(' || '.join(batch))}</strong>"
        f"<span>{'parallel workers' if len(batch) > 1 else 'dependency continuation'}</span>"
        "</div>"
        for index, batch in enumerate(review.batches)
    )
    conflict_status = "PASS" if not review.conflicts else "BLOCKED"
    return (
        "<div class='evidence review-overview'>"
        "<div class='view-heading'><div><span class='view-kicker'>AGENT COORDINATION</span>"
        f"<h2>{_escape(review.contract.title)}</h2></div>{_status(review.status)}</div>"
        + _render_contract_cards(review.contract, review.observed_result)
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>Fanout Algorithm Map</h3><span>一个进程、线程并发、隔离 worktree</span></div>"
        "<div class='algorithm-map'>"
        "<div><b>FanoutPlan</b><span>DAG + declared write_scope</span></div><i>→</i>"
        "<div><b>ThreadPoolExecutor</b><span>Future completion may vary</span></div><i>→</i>"
        "<div><b>Isolated Worktrees</b><span>AgentLoop execution is not under _git_lock</span></div><i>→</i>"
        "<div><b>Stable Merge</b><span>plan order + read-only Finalizer</span></div>"
        "</div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Observed Batches</h3><span>无绝对时间戳，不伪造时间轴</span></div>"
        f"<div class='batch-grid'>{batch_cards}"
        f"<div class='batch-card final'><small>FINAL</small><strong>Finalizer</strong>"
        f"<span>{_escape(review.final_decision)}</span></div></div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Three Conflict Gates</h3><span>设计契约与本次观测分开标注</span></div>"
        "<div class='gate-grid'>"
        "<article><small>DESIGN CONTRACT</small><b>Static Plan Gate</b>"
        "<p>depends_on + write_scope → build_conflict_free_batches</p></article>"
        f"<article><small>OBSERVED ARTIFACT</small><b>Dynamic Result Gate · {conflict_status}</b>"
        f"<p>actual touched_files；observed conflicts = {len(review.conflicts)}</p></article>"
        "<article><small>DESIGN CONTRACT</small><b>Merge Applicability Gate</b>"
        "<p>candidate diff → check_only=True → apply in stable task order</p></article>"
        "</div></section>"
        "<details class='drilldown'><summary>查看 Task Contract 与实际 touched files</summary>"
        "<div class='drilldown-body review-table-scroll'><table><thead><tr><th>任务</th>"
        "<th>依赖</th><th>允许写入</th><th>实际 touched</th><th>结果</th></tr></thead>"
        f"<tbody>{task_rows}</tbody></table></div></details>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Read-only Finalizer Contract</h3><span>只验证 integrated candidate</span></div>"
        "<div class='finalizer-contract'>"
        "<div><b>Runs only if</b><span>all planned tasks integrated · conflicts = 0</span></div>"
        "<div><b>Allowed</b><span>git_status · git_diff · python_validation</span></div>"
        "<div><b>Forbidden</b><span>candidate repair；mutation forces BLOCKED</span></div>"
        "</div></section>"
        "<div class='review-footer'>"
        "<span>Shared _git_lock: worktree prepare / cleanup only</span>"
        + _architecture_link(review.contract)
        + "</div></div>"
    )


def _render_mini50(review: Mini50Review) -> str:
    representative_cards = "".join(
        "<a class='representative-card' "
        f"href='?source={quote(item.source_key)}&amp;view=overview'>"
        f"<small>{_escape(item.classification.replace('_', ' ').upper())}</small>"
        f"<strong>{_escape(item.case_id)}</strong><span>Case {item.ordinal:02d} · open evidence →</span></a>"
        for item in review.representatives
    )
    attempts = "".join(f"<li>{_escape(item)}</li>" for item in review.attempts)
    provenance_tone = "ok" if review.evaluated_revision else "warn"
    return (
        "<div class='evidence review-overview'>"
        "<div class='view-heading'><div><span class='view-kicker'>REAL REPOSITORY CAPABILITY</span>"
        f"<h2>{_escape(review.contract.title)}</h2></div>{_status(review.status)}</div>"
        + _render_contract_cards(
            review.contract,
            f"{review.resolved}/{review.planned} official resolved；"
            f"{review.unresolved} unresolved；{review.empty_patch} empty patch",
        )
        + "<div class='canonical-metrics'>"
        f"<div><small>CANONICAL CASES</small><b>{review.planned}</b><span>exactly one terminal trajectory each</span></div>"
        f"<div class='ok'><small>RESOLVED</small><b>{review.resolved}</b><span>official evaluator</span></div>"
        f"<div class='warn'><small>UNRESOLVED</small><b>{review.unresolved}</b><span>official evaluator</span></div>"
        f"<div class='bad'><small>EMPTY PATCH</small><b>{review.empty_patch}</b><span>Agent terminal</span></div>"
        "</div>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Publish Gate Funnel</h3><span>只补基础设施无效槽位</span></div>"
        "<div class='funnel-grid'>"
        f"<div><small>INITIAL 50</small><b>{review.initial_resolved} resolved · "
        f"{review.initial_unresolved} unresolved · {review.initial_empty_patch} empty</b>"
        f"<span>{review.initial_infra_invalid} infrastructure-invalid → rejected by publish gate</span></div>"
        f"<i>infra-only completion<br>{' + '.join(map(str, review.completion_selected))} selected</i>"
        f"<div><small>FINAL 50</small><b>{review.resolved} resolved · "
        f"{review.unresolved} unresolved · {review.empty_patch} empty</b>"
        f"<span>{review.total_launches} total launches · correctness rerun = "
        f"{'yes' if review.correctness_rerun else 'no'}</span></div>"
        "</div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Representative Cases</h3><span>固定 metadata，不依赖 UI magic ids</span></div>"
        f"<div class='representative-grid'>{representative_cards}</div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>Revision Provenance</h3><span>评测结果不自动继承给当前 HEAD</span></div>"
        "<div class='revision-grid'>"
        f"<div class='{provenance_tone}'><small>EVALUATED REVISION</small>"
        f"<code>{_escape(review.evaluated_revision)}</code><span>published 28/50 belongs here</span></div>"
        f"<div><small>CURRENT REPOSITORY HEAD</small><code>{_escape(review.current_revision)}</code>"
        "<span>later changes；correctness rerun not performed</span></div>"
        "</div></section>"
        "<details class='drilldown'><summary>Attempt History · 历史尝试不进入主路径</summary>"
        f"<div class='drilldown-body'><ul class='fact-list'>{attempts or '<li>本机没有可读取的历史 attempt。</li>'}</ul>"
        "<p class='boundary-note'>历史 Run 保持 immutable；canonical resolver 只选择冻结的 50 条 terminal trajectory。</p>"
        "</div></details>"
        "<div class='review-footer'>"
        "<span>Published result: fixed Mini-50, not full SWE-bench Verified</span>"
        + _architecture_link(review.contract)
        + "</div></div>"
    )


def _render_case_anatomy(source: EvidenceSource) -> str:
    trace_path = source.trace_entries[0][1] if source.trace_entries else None
    trace = _read_json(trace_path)
    raw_events = trace.get("events")
    events = (
        [item for item in raw_events if isinstance(item, dict)]
        if isinstance(raw_events, list)
        else []
    )
    task = str(trace.get("task") or source.task)
    environment: dict[str, object] = {}
    for item in events:
        candidate = item.get("execution_environment")
        if item.get("event_type") == "execution_environment" and isinstance(
            candidate, dict
        ):
            environment = {str(key): value for key, value in candidate.items()}
            break
    repository_match = re.search(r"Repository:\s*([^\s]+)", task)
    base_match = re.search(r"Base commit:\s*([0-9a-f]{7,40})", task)
    observed_repository = str(
        environment.get("head_sha") or environment.get("git_root") or ""
    )
    if not observed_repository and base_match:
        repository = repository_match.group(1) if repository_match else "repository"
        observed_repository = f"{repository} @ {base_match.group(1)}"
    case_root = trace_path.parent if trace_path else source.run_dir
    patch_path = case_root / "candidate_changes.diff" if case_root else None
    patch_size = patch_path.stat().st_size if patch_path and patch_path.is_file() else 0
    validation_count = sum(
        item.get("event_type") == "validation_evidence" for item in events
    )
    return (
        "<div class='evidence review-overview'>"
        "<div class='view-heading'><div><span class='view-kicker'>CASE ANATOMY</span>"
        f"<h2>{_escape(source.item_key)}</h2></div>{_status(source.status)}</div>"
        "<div class='case-anatomy'>"
        f"{_anatomy_step('01', 'Problem Statement', task, 'OBSERVED ARTIFACT')}"
        f"{_anatomy_step('02', 'Repository / Base Revision', observed_repository or '未观测', 'OBSERVED ARTIFACT')}"
        f"{_anatomy_step('03', 'Agent Trajectory', f'{len(events)} Trace events', 'OBSERVED ARTIFACT')}"
        f"{_anatomy_step('04', 'Tool / Observation', '打开“执行过程”查看每轮 ToolCall 与 Observation', 'OBSERVED ARTIFACT')}"
        f"{_anatomy_step('05', 'Candidate Patch', f'{patch_size} bytes' if patch_size else 'Empty / not observed', 'OBSERVED ARTIFACT')}"
        f"{_anatomy_step('06', 'Local Validation', f'{validation_count} validation events', 'OBSERVED ARTIFACT')}"
        f"{_anatomy_step('07', 'Official Evaluator', source.status, 'OBSERVED ARTIFACT')}"
        f"{_anatomy_step('08', 'Final Classification', source.status, 'OBSERVED ARTIFACT')}"
        "</div>"
        "<p class='boundary-note'>Problem、Patch、Local 与 Official 分属不同证据层；某一层存在不自动证明下一层通过。</p>"
        f"<details class='provenance'><summary>Case raw evidence</summary><code>{_escape(str(case_root or '未产生'))}</code></details>"
        "</div>"
    )


def _anatomy_step(number: str, title: str, value: str, source_kind: str) -> str:
    return (
        "<div><b>" + _escape(number) + "</b><section><small>" + _escape(source_kind)
        + "</small><strong>" + _escape(title) + "</strong><span>" + _escape(value)
        + "</span></section></div>"
    )


def _status(value: str) -> str:
    normalized = value.lower()
    tone = "ok" if normalized in {"pass", "passed", "completed", "approved", "executed", "observed"} else (
        "bad" if normalized in {"fail", "failed", "blocked", "rejected"} else "warn"
    )
    return f"<span class='badge {tone}'>{_escape(value)}</span>"


def _short_path(path: Path | None) -> str:
    if path is None:
        return "not observed"
    parts = path.parts
    return "/".join(parts[-3:]) if len(parts) > 3 else str(path)


def _read_json(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)
