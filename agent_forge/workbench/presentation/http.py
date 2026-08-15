from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from agent_forge.bench.domain.catalog import CASE_PROFILES
from agent_forge.contracts import WORKSPACE_WRITE_TOOL_NAMES
from agent_forge.observability.api import load_run_story
from agent_forge.observability.domain.run_story import RunStory
from agent_forge.workbench.application.context_inspection import (
    ContextComponent,
    ContextTurnInspection,
    ToolDecision,
    build_context_turn_inspections,
)
from agent_forge.workbench.application.services import WorkbenchServices
from agent_forge.workbench.domain import EvidenceSource
from agent_forge.storage_layout import DEBUG_LAB_STATE_ROOT, EVALUATION_DATA_ROOT
from agent_forge.workbench.wiring import (
    build_evidence_catalog,
    build_workbench_services,
    read_evidence_json,
)

WORKBENCH_READ_ONLY_MESSAGE = (
    "Workbench 只读；执行任务请使用 forge run、demo 或 resume。"
)
WORKBENCH_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class ForgeUiHandler(BaseHTTPRequestHandler):
    """Workbench 的 HTTP 入站适配器，只处理协议与展示，不编排业务。"""

    state: WorkbenchServices

    def do_GET(self) -> None:
        """读取静态页面、运行状态和证据视图。"""

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/" or path.startswith("/index.html"):
            self._send_html(INDEX_HTML)
            return
        if path == "/api/status":
            self._send_json(self._status_payload())
            return
        if path == "/api/latest-report":
            self._send_json({"content": _read_latest_report(self.state.project_dir)})
            return
        if path == "/api/evidence":
            source_key = (query.get("source") or [""])[0]
            view = (query.get("view") or [""])[0]
            if source_key or view:
                self._send_json(
                    {
                        "html": _render_workspace_view(
                            self.state.project_dir,
                            source_key=source_key,
                            view=view or "overview",
                        )
                    }
                )
                return
            # 兼容已有书签和测试；新的 Workbench 页面只使用 source + view。
            kind = (query.get("kind") or ["summary"])[0]
            self._send_json(
                {"html": _render_evidence_html(self.state.project_dir, kind)}
            )
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """拒绝状态变更；执行入口统一留在 ``forge`` CLI/Public API。"""

        self._send_json(
            {"error": WORKBENCH_READ_ONLY_MESSAGE},
            HTTPStatus.METHOD_NOT_ALLOWED,
        )

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _status_payload(self) -> dict[str, Any]:
        evidence_sources = self.state.evidence.evidence_sources()
        return {
            "project_dir": str(self.state.project_dir),
            "python": sys.version.split()[0],
            "workbench_source_sha256": WORKBENCH_SOURCE_SHA256,
            "latest_run": str(_latest_run_dir(self.state.project_dir) or ""),
            "latest_complex": str(
                build_evidence_catalog(self.state.project_dir).latest_complex_run_dir()
                or ""
            ),
            "latest_campaign": str(_latest_campaign_dir(self.state.project_dir) or ""),
            "latest_report": _latest_report_path(self.state.project_dir),
            "feedback": _latest_feedback_outcome(self.state.project_dir),
            "selected_source": _selected_evidence_source(
                self.state.project_dir,
                evidence_sources,
            ),
            "evidence_sources": [
                source.to_public_dict() for source in evidence_sources
            ],
        }

    def _send_html(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(text.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_json(
        self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(
                json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            )
        except (BrokenPipeError, ConnectionResetError):
            return


# 主要入口：启动只读取 canonical evidence 的本地 Workbench。
def run_ui(
    host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True
) -> None:
    """启动只读 Workbench；所有执行和人工决定仍通过 CLI/Public API。"""

    project_dir = _find_project_dir(Path.cwd())
    handler = type(
        "BoundForgeUiHandler",
        (ForgeUiHandler,),
        {"state": build_workbench_services(project_dir)},
    )
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"NanoHarness 证据工作台: {url}")
    print("按 Ctrl+C 停止。")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 NanoHarness 证据工作台。")
    finally:
        server.server_close()


def build_ui_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")


def run_ui_from_args(args: argparse.Namespace) -> None:
    run_ui(host=args.host, port=args.port, open_browser=not args.no_open)


def _find_project_dir(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (
            candidate / "agent_forge"
        ).is_dir():
            return candidate
    return current


def _latest_report_path(project_dir: Path) -> str:
    return build_evidence_catalog(project_dir).latest_report_path()


def _read_latest_report(project_dir: Path) -> str:
    return build_evidence_catalog(project_dir).read_latest_report()


def _render_evidence_html(project_dir: Path, kind: str) -> str:
    if kind == "overview":
        return _render_observability_overview(project_dir)
    if kind == "summary":
        return _render_result_summary(project_dir)
    if kind == "usage":
        return _render_usage_dashboard(project_dir)
    if kind == "timeline":
        return _render_trace_timeline(project_dir)
    if kind == "orchestration_timeline":
        return _render_orchestration_trace_timeline(project_dir)
    if kind == "complex_timeline":
        return _render_complex_trace_timeline(project_dir)
    if kind == "complex_context":
        return _render_complex_context_inspector(project_dir)
    if kind == "evidence":
        return _render_run_evidence(project_dir)
    if kind == "compare":
        return _render_compare_dashboard(project_dir)
    if kind == "controls":
        return _render_runtime_controls(project_dir)
    if kind == "orchestration":
        return _render_orchestration_dashboard(project_dir)
    if kind == "complex":
        return _render_complex_lab_dashboard(project_dir)
    if kind == "evaluation":
        return _render_evaluation_dashboard(project_dir)
    if kind == "benchmark":
        return _render_benchmark_dashboard(project_dir)
    if kind == "feedback":
        return _render_feedback_dashboard(project_dir)
    if kind == "raw_report":
        return (
            f"<pre class='raw-text'>{_escape(_read_latest_report(project_dir))}</pre>"
        )
    return _empty_evidence(f"Unsupported evidence view: {kind}")


_WORKSPACE_VIEWS = {"overview", "timeline", "context", "results"}


def _render_workspace_view(
    project_dir: Path,
    *,
    source_key: str,
    view: str,
) -> str:
    """统一入口：选定一次运行后，用相同四层结构读取它的证据。"""

    catalog = build_evidence_catalog(project_dir)
    sources = catalog.evidence_sources()
    source = _find_evidence_source(
        sources,
        source_key or _selected_evidence_source(project_dir, sources),
    )
    if source is None:
        return _empty_evidence("还没有可读取的运行证据，请先执行任意 Agent 任务。")
    if not source.available:
        return _render_source_shell(
            source,
            _empty_evidence(
                f"“{source.title}”尚未产生证据。运行对应场景后刷新即可；"
                "Workbench 不会为了展示而伪造数据。"
            ),
        )

    normalized_view = view if view in _WORKSPACE_VIEWS else "overview"
    if normalized_view == "overview":
        content = _render_source_overview(project_dir, source)
    elif normalized_view == "timeline":
        content = _render_source_timeline(source)
    elif normalized_view == "context":
        content = _render_source_context(source)
    else:
        content = _render_source_results(project_dir, source)
    return _render_source_shell(source, content)


def _find_evidence_source(
    sources: tuple[EvidenceSource, ...],
    source_key: str,
) -> EvidenceSource | None:
    return next((source for source in sources if source.key == source_key), None)


def _selected_evidence_source(
    project_dir: Path,
    sources: tuple[EvidenceSource, ...],
) -> str:
    """解析稳定首页默认选择；运行脚本只更新指针，不再生成不同 URL。"""

    pointer = project_dir / DEBUG_LAB_STATE_ROOT / "workbench_source.txt"
    if pointer.is_file():
        selected_key = pointer.read_text(encoding="utf-8").strip()
        selected = _find_evidence_source(sources, selected_key)
        if selected is not None and selected.available:
            return selected.key
    canonical = _find_evidence_source(sources, "evaluation")
    if canonical is not None and canonical.available:
        canonical_summary = _read_json_file(canonical.primary_path)
        if canonical_summary.get("artifact_type") == "canonical_showcase":
            return canonical.key
    available = [source for source in sources if source.available]
    if not available:
        return sources[0].key if sources else ""
    newest = max(
        available,
        key=lambda source: (
            source.primary_path.stat().st_mtime if source.primary_path else 0.0
        ),
    )
    return newest.key


def _render_source_shell(source: EvidenceSource, content: str) -> str:
    """每个视图都保留同一份运行身份，避免切页后忘记自己在看哪次证据。"""

    source_label = {
        "runtime": "Runtime 运行",
        "scenario": "可复现场景",
        "benchmark": "评测批次",
    }.get(source.source_type, "运行证据")
    evidence_id = source.primary_path.name if source.primary_path else "未产生"
    return (
        "<div class='workspace-source'>"
        "<section class='source-identity'>"
        f"<div><span>{_escape(source_label)}</span><h2>{_escape(source.title)}</h2>"
        f"<p>{_escape(source.description)}</p></div>"
        f"{_badge(_display_value(source.status), _tone_for_status(source.status))}"
        "<dl>"
        f"<div><dt>任务</dt><dd>{_escape(source.task)}</dd></div>"
        f"<div><dt>证据 ID</dt><dd class='mono'>{_escape(evidence_id)}</dd></div>"
        "</dl>"
        "<details class='source-provenance'><summary>查看原始产物位置</summary>"
        f"<code>{_escape(str(source.primary_path or '未产生'))}</code></details>"
        "</section>"
        f"{content}</div>"
    )


def _render_source_overview(project_dir: Path, source: EvidenceSource) -> str:
    """用同一套摘要回答“这次运行是什么、发生了什么、能证明什么”。"""

    metrics, boundary = _source_overview_facts(project_dir, source)
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>RUN SUMMARY</span><h2>运行摘要</h2></div>"
        f"{_badge(_display_value(source.status), _tone_for_status(source.status))}</div>"
        "<p class='help strong'>先用这一页确认任务、状态和关键计数；需要解释过程时看“执行过程”，"
        "需要解释模型输入与动作时看“上下文与决策”，需要判断结论边界时看“结果与证据”。</p>"
        + _metric_grid(metrics)
        + "<section class='evidence-section'><div class='section-title'><h3>统一阅读顺序</h3>"
        "<span>所有运行都使用同一套读法</span></div>"
        "<div class='answer-strip overview-reading-path'>"
        "<div><b>01 运行概览</b><span>任务、状态、成本和证据是否齐全</span></div>"
        "<div><b>02 执行过程</b><span>每轮经过了哪些稳定阶段</span></div>"
        "<div><b>03 上下文与决策</b><span>输入如何变化，模型显式选择了什么动作</span></div>"
        "<div><b>04 结果与证据</b><span>产物、验证和失败结论究竟能证明到哪一层</span></div>"
        "</div></section>"
        f"<p class='boundary-note'><strong>当前证据边界：</strong>{_escape(boundary)}</p>"
        "</div>"
    )


def _source_overview_facts(
    project_dir: Path,
    source: EvidenceSource,
) -> tuple[list[tuple[str, str, str, str]], str]:
    """按来源提取少量关键事实；详细诊断留给“结果与证据”。"""

    if source.key == "orchestration":
        fanout = _read_json_file(source.primary_path)
        metrics = fanout.get("metrics") or {}
        task_count = int(metrics.get("task_count") or len(fanout.get("results") or []))
        completed_count = int(metrics.get("completed_count") or 0)
        conflicts = len(fanout.get("conflicts") or [])
        batches = len(fanout.get("batches") or [])
        return (
            [
                (
                    "任务完成",
                    f"{completed_count}/{task_count}",
                    "Worker 最终状态",
                    "ok" if task_count and completed_count == task_count else "warn",
                ),
                ("并发批次", str(batches), "按依赖和写入范围分组", "neutral"),
                (
                    "范围冲突",
                    str(conflicts),
                    "合并前确定性门禁",
                    "bad" if conflicts else "ok",
                ),
                (
                    "Agent Trace",
                    str(len(source.trace_entries)),
                    "Worker 与 Finalizer",
                    "neutral",
                ),
            ],
            "这里只证明这次显式计划的依赖、隔离、合并和 Finalizer 结果；不外推通用多 Agent 收益。",
        )

    if source.key in {"evaluation", "evaluation-history"}:
        quality_summary = _read_json_file(
            source.primary_path
            if source.primary_path is not None and source.primary_path.is_file()
            else None
        )
        if quality_summary.get("artifact_type") == "canonical_showcase":
            return _canonical_showcase_overview_facts(quality_summary)
        if quality_summary.get("artifact_type") == "quality_selection_incident":
            return _quality_selection_incident_overview_facts(quality_summary)
        if quality_summary.get("experiment_type") == "runtime_quality":
            schema_version = int(quality_summary.get("schema_version") or 1)
            if schema_version < 2:
                historical_iterations = [
                    item
                    for item in quality_summary.get("iterations") or []
                    if isinstance(item, dict)
                ]
                return (
                    [
                        (
                            "证据等级",
                            "Pre-R0",
                            "旧 schema 仅保留探索信号",
                            "warn",
                        ),
                        (
                            "Official 主指标",
                            "不可用",
                            "裁决与复现协议未完整冻结",
                            "warn",
                        ),
                        (
                            "历史候选轮",
                            str(len(historical_iterations)),
                            "不进入正式 R0-R3 因果链",
                            "neutral",
                        ),
                        (
                            "旧 accepted 标签",
                            "已撤回",
                            "candidate 与过程指标不替代 official resolved",
                            "warn",
                        ),
                    ],
                    "这是迁移保留的 Pre-R0 探索性摘要；Workbench 会 fail-closed，不把旧 accepted_iteration 提升为正式参考。",
                )
            phase2 = _phase2_summary(quality_summary)
            if phase2:
                case_study = _mapping(phase2.get("case_study"))
                target_baseline = _mapping(case_study.get("baseline_metrics"))
                target_treatment = _mapping(case_study.get("treatment_metrics"))
                guards = _mapping(phase2.get("guards"))
                guard_metrics = _mapping(guards.get("metrics"))
                golden = _mapping(phase2.get("golden10_expansion"))
                golden_baseline = _mapping(golden.get("baseline_metrics"))
                golden_treatment = _mapping(golden.get("treatment_metrics"))
                activation_observed = _optional_metric(
                    target_treatment,
                    "mechanism_activation_observed",
                    "activation_observed",
                )
                if activation_observed is None:
                    activation_observed = _optional_metric(
                        case_study,
                        "mechanism_activation_observed",
                        "activation_observed",
                    )
                activation_expected = _optional_metric(
                    target_treatment,
                    "mechanism_activation_expected",
                    "activation_expected",
                )
                if activation_expected is None:
                    activation_expected = _optional_metric(
                        case_study,
                        "mechanism_activation_expected",
                        "activation_expected",
                    )
                return (
                    [
                        (
                            "Target 个案",
                            _quality_transition(target_baseline, target_treatment),
                            "历史基线 → 固定 Treatment；独立分母",
                            _quality_result_tone(target_treatment),
                        ),
                        (
                            "机制激活",
                            _optional_ratio(activation_observed, activation_expected),
                            "Operation Ledger 记号实际观测 / 预期",
                            "ok"
                            if activation_expected is not None
                            and activation_expected > 0
                            and activation_observed == activation_expected
                            else "warn",
                        ),
                        (
                            "正确性 Guards",
                            _quality_result(guard_metrics),
                            "原已解决样本；不与 Target 合并分母",
                            _quality_result_tone(guard_metrics),
                        ),
                        (
                            "Golden-10 扩展",
                            _quality_transition(golden_baseline, golden_treatment),
                            "仅完整扩展可支持总体样本内对比",
                            _quality_result_tone(golden_treatment),
                        ),
                    ],
                    "Phase 2 先报告 post-hoc Case-level 机制证据；Target、Guards 和 Golden-10 各自保留分母，不外推 SWE-bench Verified 总体提升。",
                )
            reference_iteration = str(
                quality_summary.get("reference_iteration") or "R0"
            )
            metrics = _quality_reference_metrics(quality_summary)
            planned = _quality_metric(
                metrics, "planned", "case_count", "official_denominator"
            )
            resolved = _quality_metric(metrics, "official_resolved", "confirmed_solved")
            decided = _quality_decided(metrics, planned)
            patch_count = int(metrics.get("patch_generated") or 0)
            failed_calls = int(metrics.get("failed_tool_calls") or 0)
            tool_calls = int(metrics.get("tool_calls") or 0)
            accepted = str(quality_summary.get("accepted_iteration") or "")
            candidate_iterations = [
                item
                for item in quality_summary.get("iterations") or []
                if isinstance(item, dict)
                and str(item.get("id") or "") != reference_iteration
            ]
            rejected_count = sum(
                str(item.get("decision") or "").lower() == "rejected"
                for item in candidate_iterations
            )
            return (
                [
                    (
                        f"正式 {reference_iteration} 解决 / 计划",
                        f"{resolved}/{planned}",
                        f"官方裁决覆盖 {decided}/{planned}",
                        "ok" if resolved else "warn",
                    ),
                    (
                        "候选改动",
                        f"{patch_count}/{planned}",
                        "生成 Patch 不等于解决",
                        "neutral",
                    ),
                    (
                        "失败工具调用",
                        f"{failed_calls}/{tool_calls}",
                        "只统计真实 Tool Observation",
                        "warn" if failed_calls else "ok",
                    ),
                    (
                        "候选优化",
                        accepted or "0 轮采纳",
                        f"{rejected_count}/{len(candidate_iterations)} 轮按预注册 gate 拒绝",
                        "ok"
                        if accepted and accepted != reference_iteration
                        else "warn",
                    ),
                ],
                "主指标始终是 resolved / planned。Sentinel 与 Golden-10 分母不同，不做百分比横比，也不外推为 SWE-bench Verified 总体解决率。",
            )

        summary = _latest_campaign_summary(project_dir)
        state = _latest_campaign_state(project_dir)
        paired = summary.get("paired_sample") or summary.get("paired_official") or {}
        status_counts = summary.get("status_counts") or {}
        records = [
            item for item in state.get("records") or [] if isinstance(item, dict)
        ]
        case_count = len(
            {str(item.get("case_id")) for item in records if item.get("case_id")}
        )
        variant_count = len(summary.get("variants") or {})
        planned = int(summary.get("planned_runs") or 0)
        completed = int(status_counts.get("completed") or 0)
        return (
            [
                (
                    "运行槽位",
                    f"{completed}/{planned}",
                    "Case × 配置 × 重复",
                    "ok" if planned and completed == planned else "warn",
                ),
                ("Case", str(case_count), "当前批次实际覆盖", "neutral"),
                ("配置", str(variant_count), "受控变量对比", "neutral"),
                (
                    "可裁决配对",
                    str(
                        int(
                            paired.get("adjudicated_pairs")
                            or paired.get("evaluated_pairs")
                            or 0
                        )
                    ),
                    "基础设施故障单独排除",
                    "ok"
                    if paired.get("adjudicated_pairs") or paired.get("evaluated_pairs")
                    else "warn",
                ),
            ],
            "批次只支持当前预注册样本和配置的比较；不能写成官方排行榜或总体解决率。",
        )

    usage = _read_json_file(source.usage_path)
    summary = usage.get("summary") or {}
    trace_path = source.trace_entries[0][1] if source.trace_entries else None
    trace = _read_json_file(trace_path)
    events = _event_list(trace)
    turns = {
        int(event.get("step") or 0)
        for event in events
        if int(event.get("step") or 0) > 0
    }
    checkpoints = sum(
        event.get("event_type") == "task_state_checkpoint" for event in events
    )
    failed_tools = int(summary.get("failed_tool_calls") or 0)
    return (
        [
            ("Agent Turn", str(len(turns)), "真实模型边界", "neutral"),
            (
                "模型调用",
                str(int(summary.get("llm_calls") or 0)),
                "实际请求次数",
                "neutral",
            ),
            (
                "工具调用",
                str(int(summary.get("tool_calls") or 0)),
                f"失败 {failed_tools} 次",
                "bad" if failed_tools else "ok",
            ),
            ("Checkpoint", str(checkpoints), "可恢复状态边界", "neutral"),
        ],
        "本页描述单次 Runtime 事实；候选改动、本地验证和官方解决必须继续分层判断。",
    )


def _render_source_timeline(source: EvidenceSource) -> str:
    if not source.trace_entries:
        return _empty_evidence(
            "当前发布包没有 Turn 级 Trace。它仍可用于结果与改进复盘，但不能展示模型轮次。"
        )
    visible_entries = list(source.trace_entries[:8])
    hidden_count = max(len(source.trace_entries) - len(visible_entries), 0)
    title = "执行时间线"
    if source.key == "orchestration":
        title = "Worker 与 Finalizer 执行时间线"
    rendered = _render_trace_timeline_entries(
        visible_entries,
        scope_label=source.title,
        title=title,
        source_path=source.primary_path,
    )
    if hidden_count:
        rendered += (
            "<p class='boundary-note'>为控制阅读噪音，本页只展开前 8 条 Trace；"
            f"还有 {hidden_count} 条可在原始评测目录中按 Case 查看。</p>"
        )
    return rendered


def _render_source_context(source: EvidenceSource) -> str:
    """让所有 AgentLoop Trace 共享同一套 Context -> Decision -> Feedback 投影。"""

    if not source.trace_entries:
        return _empty_evidence(
            "当前证据没有 AgentLoop Trace，因此无法还原逐轮上下文。"
            "这不是 0 次调用，而是该发布包没有保存这一层证据。"
        )
    panels: list[str] = []
    for index, (label, trace_path) in enumerate(source.trace_entries[:8]):
        trace = _read_json_file(trace_path)
        turns = build_context_turn_inspections(trace)
        if not turns:
            panels.append(
                "<details class='trace-context-unit'"
                + (" open" if index == 0 else "")
                + f"><summary>{_escape(label)} · 无可投影 Turn</summary>"
                f"<p class='empty-inline'>{_escape(str(trace_path))}</p></details>"
            )
            continue
        panels.append(
            _render_context_trace_panel(
                label=label,
                trace_path=trace_path,
                trace=trace,
                turns=turns,
                open_panel=index == 0,
            )
        )
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>CONTEXT LENS</span><h2>上下文与决策</h2></div>"
        "<span class='claim-note'>可审计输入与动作，不是隐藏思维链</span></div>"
        "<p class='help strong'>统一按四步阅读：上一轮新增证据、当前输入组成、"
        "模型显式决定、工具反馈。普通运行、复杂任务和每个 Worker 都使用同一投影。</p>"
        f"{''.join(panels)}</div>"
    )


def _render_context_trace_panel(
    *,
    label: str,
    trace_path: Path,
    trace: dict[str, Any],
    turns: tuple[ContextTurnInspection, ...],
    open_panel: bool,
) -> str:
    key_turns = [turn for turn in turns if turn.is_key_turn]
    peak_tokens = max((turn.estimated_tokens for turn in turns), default=0)
    compacted_turns = sum(turn.compacted for turn in turns)
    tool_names = {
        decision.tool_name for turn in turns for decision in turn.tool_decisions
    }
    key_links = "".join(
        "<a class='context-jump' "
        f"href='#context-{_safe_html_id(label)}-{turn.step}'><b>Turn {turn.step}</b>"
        f"<span>{_escape(turn.key_reason)}</span></a>"
        for turn in key_turns
    )
    turn_blocks = "".join(
        _render_context_turn(
            turn,
            element_id=f"context-{_safe_html_id(label)}-{turn.step}",
        )
        for turn in turns
    )
    task = str(trace.get("task") or "未记录任务")
    open_attribute = " open" if open_panel else ""
    return (
        f"<details class='trace-context-unit'{open_attribute}>"
        f"<summary><b>{_escape(label)}</b><span>{len(turns)} Turn · "
        f"{len(key_turns)} 个关键转折 · 峰值 {peak_tokens:,} tokens</span></summary>"
        "<div class='trace-context-body'>"
        f"<p class='task-summary'><span>任务</span>{_escape(task)}</p>"
        + _metric_grid(
            [
                ("Agent Turn", str(len(turns)), "真实模型边界", "neutral"),
                ("关键转折", str(len(key_turns)), "优先展开", "ok"),
                ("上下文压缩", str(compacted_turns), "触发压缩的 Turn", "neutral"),
                ("实际工具", str(len(tool_names)), "实际调用种类", "neutral"),
            ]
        )
        + (f"<div class='context-jumps'>{key_links}</div>" if key_links else "")
        + f"<div class='context-turns'>{turn_blocks}</div>"
        "<details class='provenance'><summary>证据来源</summary>"
        f"<code>{_escape(str(trace_path))}</code></details>"
        "</div></details>"
    )


def _safe_html_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "trace"


def _render_source_results(project_dir: Path, source: EvidenceSource) -> str:
    if source.key == "governed":
        return _render_runtime_controls(project_dir)
    if source.key == "orchestration":
        summary = _read_json_file(source.primary_path)
        return _render_fanout_run_evidence(summary, source.primary_path)
    if source.key in {"evaluation", "evaluation-history"}:
        quality_summary = _read_json_file(
            source.primary_path
            if source.primary_path is not None and source.primary_path.is_file()
            else None
        )
        if quality_summary.get("artifact_type") == "canonical_showcase":
            return _render_canonical_showcase_dashboard(
                quality_summary,
                source.primary_path,
            )
        if quality_summary.get("artifact_type") == "quality_selection_incident":
            return _render_quality_selection_incident_dashboard(
                quality_summary,
                source.primary_path,
            )
        if quality_summary.get("experiment_type") == "runtime_quality":
            return _render_runtime_quality_dashboard(
                quality_summary,
                source.primary_path,
            )
        return _render_benchmark_dashboard(project_dir, include_canonical=False)
    return _render_single_source_results(source)


def _render_single_source_results(source: EvidenceSource) -> str:
    """渲染一条 Single-Run 的状态、成本、产物与证据等级。"""

    usage = _read_json_file(source.usage_path)
    summary = usage.get("summary") or {}
    trace_path = source.trace_entries[0][1] if source.trace_entries else None
    trace = _read_json_file(trace_path)
    story: RunStory | None = None
    story_error = ""
    if source.run_dir is not None and (source.run_dir / "run_manifest.json").is_file():
        try:
            # latest 指针可能指向别的运行；直接从当前目录加载才不会串证据。
            story = load_run_story(source.run_dir)
        except (OSError, ValueError) as exc:
            story_error = str(exc)
    checkpoint_count = int(
        summary.get("checkpoints")
        or summary.get("task_state_checkpoints")
        or sum(
            event.get("event_type") == "task_state_checkpoint"
            for event in _event_list(trace)
        )
    )
    cost = float(summary.get("estimated_cost_usd") or 0.0)
    return (
        "<div class='evidence'><div class='view-heading'><div>"
        "<span class='view-kicker'>RESULTS & EVIDENCE</span><h2>结果与证据边界</h2></div>"
        f"{_badge(_display_value(source.status), _tone_for_status(source.status))}</div>"
        + _metric_grid(
            [
                (
                    "模型调用",
                    str(summary.get("llm_calls", 0)),
                    "实际请求次数",
                    "neutral",
                ),
                (
                    "工具调用",
                    str(summary.get("tool_calls", 0)),
                    f"失败 {int(summary.get('failed_tool_calls') or 0)} 次",
                    "bad" if summary.get("failed_tool_calls") else "ok",
                ),
                ("Checkpoint", str(checkpoint_count), "持久化状态边界", "neutral"),
                ("估算成本", f"${cost:.6f}", "当前运行", "neutral"),
            ]
        )
        + _render_run_story_section(story, source.run_dir, story_error)
        + "<details class='provenance'><summary>运行产物目录</summary>"
        f"<code>{_escape(str(source.run_dir or '未找到'))}</code></details></div>"
    )


def _latest_run_dir(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_run_dir()


def _latest_run_story(project_dir: Path) -> RunStory | None:
    return build_evidence_catalog(project_dir).latest_run_story()


def _latest_governed_run_story(project_dir: Path) -> RunStory | None:
    return build_evidence_catalog(project_dir).latest_governed_run_story()


def _latest_governed_trace_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_governed_trace_path()


def _latest_governed_usage_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_governed_usage_path()


def _latest_complex_run_dir(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_complex_run_dir()


def _latest_complex_run_story(project_dir: Path) -> RunStory | None:
    return build_evidence_catalog(project_dir).latest_complex_run_story()


def _latest_complex_trace_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_complex_trace_path()


def _latest_complex_usage_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_complex_usage_path()


def _latest_trace_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_trace_path()


def _latest_usage_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_usage_path()


def _latest_comparison_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_comparison_path()


def _latest_multi_agent_summary_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_multi_agent_summary_path()


def _latest_fanout_summary_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_fanout_summary_path()


def _latest_orchestration_summary_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_orchestration_summary_path()


def _latest_orchestration_fanout_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_orchestration_fanout_path()


def _latest_benchmark_run_dir(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_benchmark_run_dir()


def _latest_benchmark_comparison_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_benchmark_comparison_path()


def _latest_benchmark_multi_agent_summary_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(
        project_dir
    ).latest_benchmark_multi_agent_summary_path()


def _latest_benchmark_usage_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_benchmark_usage_path()


def _trace_paths_for_timeline(project_dir: Path) -> list[tuple[str, Path]]:
    """Timeline 优先展示 Lab 1 的真实 AgentLoop，避免被 Fanout 摘要覆盖。"""

    catalog = build_evidence_catalog(project_dir)
    governed_trace = catalog.latest_governed_trace_path()
    if governed_trace is not None:
        return [("受治理 AgentLoop", governed_trace)]
    return catalog.trace_paths()


def _latest_feedback_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_feedback_path()


def _latest_feedback_outcome(project_dir: Path) -> str:
    return build_evidence_catalog(project_dir).latest_feedback_outcome()


def _latest_result_record(project_dir: Path) -> dict[str, Any]:
    return build_evidence_catalog(project_dir).latest_result_record()


def _latest_campaign_dir(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_campaign_dir()


def _latest_campaign_state(project_dir: Path) -> dict[str, Any]:
    return build_evidence_catalog(project_dir).latest_campaign_state()


def _latest_campaign_summary(project_dir: Path) -> dict[str, Any]:
    return build_evidence_catalog(project_dir).latest_campaign_summary()


def _canonical_showcase_summary_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).canonical_showcase_summary_path()


def _latest_improvement_record_path(project_dir: Path) -> Path | None:
    return build_evidence_catalog(project_dir).latest_improvement_record_path()


def _event_list(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in trace.get("events") or [] if isinstance(event, dict)]


def _string_items(value: Any) -> list[str]:
    """把证据字段收敛成可展示的非空字符串列表。"""

    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in items if str(item).strip()]


def _render_fact_list(values: Any, *, empty_message: str) -> str:
    """逐行展示复合事实，避免权限、工具和文件被挤成一段文本。"""

    items = _string_items(values)
    if not items:
        items = [empty_message]
    return (
        "<ul class='fact-list'>"
        + "".join(f"<li>{_escape(item)}</li>" for item in items)
        + "</ul>"
    )


def _render_lab_brief(
    *,
    question: str,
    input_label: str,
    input_items: Any,
    mechanism: str,
    success_criteria: str,
    boundary: str,
) -> str:
    """先交代实验问题和输入，再允许读者查看指标与底层事件。"""

    return (
        "<section class='lab-brief'>"
        "<div class='lab-question'><span>这次运行要回答的问题</span>"
        f"<strong>{_escape(question)}</strong></div>"
        "<div class='lab-brief-grid'>"
        f"<div><b>{_escape(input_label)}</b>"
        f"{_render_fact_list(input_items, empty_message='没有可读取的输入证据')}</div>"
        f"<div><b>关键机制</b><p>{_escape(mechanism)}</p></div>"
        f"<div><b>通过标准</b><p>{_escape(success_criteria)}</p></div>"
        f"<div><b>证据边界</b><p>{_escape(boundary)}</p></div>"
        "</div></section>"
    )


def _last_event(trace: dict[str, Any], *event_types: str) -> dict[str, Any]:
    allowed = set(event_types)
    for event in reversed(_event_list(trace)):
        if str(event.get("event_type") or "") in allowed:
            return event
    return {}


def _read_json_file(path: Path | None) -> dict[str, Any]:
    return read_evidence_json(path)


def _render_observability_overview(project_dir: Path) -> str:
    """汇总三类运行证据和可选评测档案，首页只回答结果与入口。"""

    run_story = _latest_governed_run_story(project_dir)
    trace = _read_json_file(_latest_governed_trace_path(project_dir))
    usage = _read_json_file(_latest_governed_usage_path(project_dir))
    usage_summary = usage.get("summary") or {}
    events = _event_list(trace)
    turns = {
        int(event.get("step") or 0)
        for event in events
        if int(event.get("step") or 0) > 0
    }

    fanout_path = _latest_orchestration_fanout_path(project_dir)
    fanout = _read_json_file(fanout_path)
    orchestration_path = _latest_orchestration_summary_path(project_dir)
    orchestration = fanout or _read_json_file(orchestration_path)
    orchestration_status = str(orchestration.get("status") or "not_observed")
    orchestration_metrics = orchestration.get("metrics") or {}

    campaign = _latest_campaign_summary(project_dir)
    campaign_state = _latest_campaign_state(project_dir)
    campaign_records = [
        record
        for record in campaign_state.get("records") or []
        if isinstance(record, dict)
    ]
    campaign_cases = {
        str(record.get("case_id") or "")
        for record in campaign_records
        if record.get("case_id")
    }
    paired = campaign.get("paired_sample") or campaign.get("paired_official") or {}

    run_status = (
        run_story.status
        if run_story is not None
        else str(trace.get("stop_reason") or "not_observed")
    )
    run_id = (
        run_story.run_id
        if run_story is not None
        else str(trace.get("run_id") or "未找到")
    )
    failed_tools = int(usage_summary.get("failed_tool_calls") or 0)
    checkpoint_count = sum(
        1 for event in events if event.get("event_type") == "task_state_checkpoint"
    )
    task_count = int(
        orchestration_metrics.get("task_count")
        or len(orchestration.get("role_results") or [])
    )
    complex_story = _latest_complex_run_story(project_dir)
    complex_trace = _read_json_file(_latest_complex_trace_path(project_dir))
    complex_usage = _read_json_file(_latest_complex_usage_path(project_dir))
    complex_summary = complex_usage.get("summary") or {}
    complex_events = _event_list(complex_trace)
    complex_turns = {
        int(event.get("step") or 0)
        for event in complex_events
        if int(event.get("step") or 0) > 0
    }
    complex_status = (
        complex_story.status
        if complex_story is not None
        else str(complex_summary.get("latest_task_status") or "not_observed")
    )
    displayed_failed_tools = (
        int(complex_summary.get("failed_tool_calls") or 0)
        if complex_summary
        else failed_tools
    )
    paired_count = int(
        paired.get("adjudicated_pairs") or paired.get("evaluated_pairs") or 0
    )
    evidence_state = "official" if paired_count else "fact"
    evidence_text = f"{paired_count} 组可裁决配对" if paired_count else "只有运行事实"

    hierarchy = (
        "<div class='scope-hierarchy'>"
        f"<div><b>01</b><span>实验批次</span><strong>{len(campaign_cases)} 个 Case</strong>"
        "<small>跨运行比较</small></div>"
        f"<div><b>02</b><span>单次运行</span><strong>{_escape(_display_value(run_status))}</strong>"
        f"<small class='mono'>{_escape(run_id[:18])}</small></div>"
        f"<div><b>03</b><span>Agent 轮次</span><strong>{len(turns)} 轮</strong>"
        "<small>一次模型决策</small></div>"
        "<div><b>04</b><span>语义阶段</span><strong>固定 6 类</strong>"
        "<small>上下文到持久化</small></div>"
        f"<div><b>05</b><span>原始事件</span><strong>{len(events)} 条</strong>"
        "<small>仅排障时展开</small></div>"
        "</div>"
    )
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>观测总览</span>"
        "<h2>先看结论，再逐层下钻</h2></div>"
        f"{_badge(evidence_state, 'ok' if paired_count else 'neutral')}</div>",
        "<p class='help strong'>当前页面分别索引受治理运行、多 Agent 协同、复杂真实任务和可选评测档案。每条主线使用独立指针，不会因运行顺序相互覆盖。</p>",
        _metric_grid(
            [
                (
                    "受治理运行",
                    _display_value(run_status),
                    f"{len(turns)} 轮 · {checkpoint_count} 个 Checkpoint",
                    _tone_for_status(run_status),
                ),
                (
                    "多 Agent 协同",
                    _display_value(orchestration_status),
                    f"{task_count} 个任务 · 独立证据范围",
                    _tone_for_status(orchestration_status),
                ),
                (
                    "复杂真实任务",
                    _display_value(complex_status),
                    f"{len(complex_turns)} 轮 · 真实模型",
                    _tone_for_status(complex_status),
                ),
                (
                    "可选评测档案",
                    evidence_text,
                    "候选、本地、官方分层",
                    "ok" if paired_count else "warn",
                ),
                (
                    "失败工具调用",
                    str(displayed_failed_tools),
                    "优先来自复杂真实任务",
                    "bad" if displayed_failed_tools else "ok",
                ),
                (
                    "复杂任务底层事件",
                    str(len(complex_events)),
                    "默认折叠，只用于定位原因",
                    "neutral",
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>证据层级</h3>"
        "<span>批次 → 运行 → 轮次 → 阶段 → 事件</span></div>",
        hierarchy,
        "</section>",
        "<section class='evidence-section'><div class='section-title'><h3>三个必学场景</h3>"
        "<span>先获得实践经验，再考虑展示</span></div>",
        "<div class='scenario-grid'>"
        "<button onclick=\"loadEvidence('controls')\"><b>1</b><strong>受治理运行</strong>"
        f"<span>{checkpoint_count} 个 Checkpoint · 权限、HITL、恢复</span></button>"
        "<button onclick=\"loadEvidence('orchestration')\"><b>2</b><strong>多 Agent 协同</strong>"
        f"<span>{task_count} 个任务 · 隔离、合并、冲突控制</span></button>"
        "<button onclick=\"loadEvidence('complex')\"><b>3</b><strong>复杂真实任务</strong>"
        f"<span>{len(complex_turns)} 轮 · 检索、失败反馈、修正、回归</span></button>"
        "</div></section>",
        "<section class='evidence-section'><div class='section-title'><h3>阅读原则</h3>"
        "<span>观测面与底层事实分开</span></div>"
        "<div class='answer-strip'>"
        "<div><b>结果</b><span>先看状态和证据上限</span></div>"
        "<div><b>原因</b><span>再看轮次与四段主链</span></div>"
        "<div><b>细节</b><span>最后才展开事件和文件来源</span></div>"
        "</div></section>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_result_summary(project_dir: Path) -> str:
    run_dir = _latest_run_dir(project_dir)
    if not run_dir:
        return _empty_evidence("还没有运行产物，请先执行一个 Lab 或 Agent 任务。")

    fanout_path = _latest_fanout_summary_path(project_dir)
    fanout = _read_json_file(fanout_path)
    if fanout:
        return _render_fanout_result_summary(fanout, fanout_path)

    results = _read_json_file(run_dir / "results.json")
    usage = _read_json_file(_latest_usage_path(project_dir))
    trace = _read_json_file(_latest_trace_path(project_dir))
    summary = usage.get("summary") or {}
    report_path = _latest_report_path(project_dir) or "未找到"

    if results:
        cases = results.get("case_results") or []
        patch_count = sum(1 for case in cases if int(case.get("patch_chars") or 0) > 0)
        status_text = (
            ", ".join(
                f"{case.get('instance_id')}: {_display_value(case.get('status'))}"
                for case in cases
            )
            or "没有 Case"
        )
        case_rows = "".join(
            "<tr>"
            f"<td class='mono'>{_escape(case.get('instance_id', ''))}</td>"
            f"<td>{_escape(case.get('repo', ''))}</td>"
            f"<td>{_badge(case.get('status', ''), _tone_for_status(case.get('status', '')))}</td>"
            f"<td>{_badge(case.get('failure_class', 'unclassified'), _tone_for_status(case.get('failure_class', '')))}</td>"
            f"<td>{_badge(case.get('evaluation_status', ''), _tone_for_status(case.get('evaluation_status', '')))}</td>"
            f"<td>{int(case.get('patch_chars') or 0)}</td>"
            f"<td>{_escape(_translate_evidence_text(case.get('diagnosis', '')))}</td>"
            f"<td>{_escape(_translate_evidence_text((case.get('next_actions') or [''])[0]))}</td>"
            "</tr>"
            for case in cases
        )
        case_rows_html = case_rows or "<tr><td colspan='8'>没有 Case</td></tr>"
        body = [
            "<h2>代码仓任务结果</h2>",
            "<p class='help strong'>从 results.json、usage.json 与 trace.json 汇总任务结果；原始产物仍保留用于追溯。</p>",
            _metric_grid(
                [
                    (
                        "运行 ID",
                        results.get("run_id", ""),
                        "本次基准运行的唯一标识",
                        "neutral",
                    ),
                    (
                        "模型服务",
                        (
                            f"{results.get('provider', '')}/{results.get('model') or 'default'} "
                            f"· T={float(results.get('temperature') or 0):g}"
                        ),
                        "真实模型与采样配置",
                        "ok",
                    ),
                    (
                        "Case 数量",
                        str(len(cases)),
                        "本次运行的 SWE-bench 任务数",
                        "neutral",
                    ),
                    (
                        "候选改动",
                        f"{patch_count}/{len(cases)}",
                        "产生候选 diff 的任务数",
                        "ok" if patch_count else "warn",
                    ),
                    (
                        "结束状态",
                        status_text,
                        "Agent 的最终状态",
                        _tone_for_status(status_text),
                    ),
                    (
                        "估算成本",
                        f"${float(summary.get('estimated_cost_usd') or 0):.6f}",
                        "DeepSeek 估算费用",
                        "ok",
                    ),
                ]
            ),
            "<h3>固定演示 Case</h3>",
            "<p>默认参考 Case 为 <span class='mono'>astropy__astropy-12907</span>：真实的 Astropy 嵌套模型可分离性缺陷，用于观察上下文检索、工具选择、循环控制和成本统计。</p>",
            f"<p><span class='label'>最新报告</span><span class='mono'>{_escape(report_path)}</span></p>",
            "<h3>Case 明细</h3>",
            "<table><thead><tr><th>实例</th><th>代码仓</th><th>Agent 状态</th><th>失败分类</th><th>评测状态</th><th>改动字符数</th><th>诊断</th><th>下一步</th></tr></thead>"
            f"<tbody>{case_rows_html}</tbody></table>",
        ]
    else:
        body = [
            "<h2>代码仓任务结果</h2>",
            _metric_grid(
                [
                    ("运行 ID", usage.get("run_id", ""), "普通 Agent 运行", "neutral"),
                    (
                        "停止原因",
                        _display_value(usage.get("stop_reason", "")),
                        "为什么结束本次运行",
                        _tone_for_status(usage.get("stop_reason", "")),
                    ),
                    (
                        "模型调用",
                        str(summary.get("llm_calls", 0)),
                        "调用大模型的次数",
                        "neutral",
                    ),
                    (
                        "Token 总量",
                        str(summary.get("total_tokens", 0)),
                        "输入与输出 Token 总和",
                        "neutral",
                    ),
                    (
                        "估算成本",
                        f"${float(summary.get('estimated_cost_usd') or 0):.6f}",
                        "模型调用估算费用",
                        "ok",
                    ),
                    (
                        "工具调用",
                        str(summary.get("tool_calls", 0)),
                        "执行工具的总次数",
                        "neutral",
                    ),
                ]
            ),
            f"<p><span class='label'>任务</span>{_escape((usage.get('task') or trace.get('task') or '')[:800])}</p>",
        ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_usage_dashboard(project_dir: Path) -> str:
    fanout_path = _latest_fanout_summary_path(project_dir)
    fanout = _read_json_file(fanout_path)
    if fanout:
        return _render_fanout_usage_dashboard(fanout, fanout_path)

    usage_path = _latest_usage_path(project_dir)
    usage = _read_json_file(usage_path)
    if not usage:
        return _empty_evidence("没有找到 usage.json，请先运行 Agent 或一个 Debug Lab。")

    summary = usage.get("summary") or {}
    rows = []
    for step in usage.get("steps") or []:
        calls = step.get("llm_calls") or []
        prompt = sum(int(call.get("prompt_tokens") or 0) for call in calls)
        completion = sum(int(call.get("completion_tokens") or 0) for call in calls)
        cost = sum(float(call.get("estimated_cost_usd") or 0) for call in calls)
        actions = step.get("actions") or []
        action_text = ", ".join(
            f"{action.get('tool', '工具')}:{'成功' if action.get('success') else '失败'}"
            for action in actions[:4]
        )
        if len(actions) > 4:
            action_text += f", +{len(actions) - 4}"
        rows.append(
            "<tr>"
            f"<td>{int(step.get('step') or 0)}</td>"
            f"<td>{len(calls)}</td>"
            f"<td>{prompt}</td>"
            f"<td>{completion}</td>"
            f"<td>${cost:.6f}</td>"
            f"<td>{int((step.get('context') or {}).get('total_chars') or 0)}</td>"
            f"<td>{_escape(action_text or '无')}</td>"
            "</tr>"
        )

    context_sections = (usage.get("context_breakdown") or {}).get("section_chars") or {}
    context_rows = "".join(
        f"<tr><td>{_escape(_display_context_section(name))}</td><td>{int(value)}</td></tr>"
        for name, value in sorted(
            context_sections.items(), key=lambda item: int(item[1]), reverse=True
        )
    )
    tools = (usage.get("tool_efficiency") or {}).get("by_tool") or {}
    tool_rows = "".join(
        "<tr>"
        f"<td>{_escape(name)}</td>"
        f"<td>{data.get('calls', 0)}</td>"
        f"<td>{data.get('success', 0)}</td>"
        f"<td>{data.get('failed', 0)}</td>"
        f"<td>{int(data.get('duration_ms', 0) or 0)}</td>"
        "</tr>"
        for name, data in tools.items()
    )
    step_rows_html = "".join(rows) or "<tr><td colspan='7'>没有步骤数据</td></tr>"
    context_rows_html = context_rows or "<tr><td colspan='2'>没有上下文数据</td></tr>"
    tool_rows_html = tool_rows or "<tr><td colspan='5'>没有工具数据</td></tr>"
    active_skills = summary.get("active_skills") or []
    adaptive_rows = "".join(
        [
            "<tr><td>结构化上下文压缩</td>"
            f"<td>{int(summary.get('compacted_context_turns') or 0)} 个 Turn</td>"
            f"<td>{int(summary.get('context_overflow_recoveries') or 0)} 次上下文溢出恢复</td>"
            "<td>SessionDigest + 原始 Trace 来源</td></tr>",
            "<tr><td>有证据的长期记忆召回</td>"
            f"<td>{int(summary.get('memory_recalled') or 0)} 条记录</td>"
            "<td>排除候选记录，只召回已生效记录</td>"
            "<td>LongTermMemoryService</td></tr>",
            "<tr><td>工具调用规范化</td>"
            f"<td>{int(summary.get('tool_call_repairs') or 0)} 次修复</td>"
            "<td>仅修复可见工具名和确定性参数格式</td>"
            "<td>ModelGateway + ToolCallNormalizer</td></tr>",
            "<tr><td>单轮工具调用上限</td>"
            f"<td>{int(summary.get('bounded_tool_call_bursts') or 0)} 次超限拦截</td>"
            "<td>超过上限的调用不会执行</td>"
            "<td>ToolExecutionPipeline</td></tr>",
            "<tr><td>Skill 激活</td>"
            f"<td>{_escape(', '.join(str(item) for item in active_skills) or '本次未观测')}</td>"
            "<td>可在预注册同任务运行中观察激活前后的差异</td>"
            "<td>SkillRegistry + 评测记分卡</td></tr>",
        ]
    )

    body = [
        "<h2>成本与工具效率：工程量化证据</h2>",
        "<p class='help strong'>这里回答一次真实运行花了多少 token、多少钱、哪里消耗上下文，以及工具调用是否高效。</p>",
        _metric_grid(
            [
                (
                    "模型调用",
                    str(summary.get("llm_calls", 0)),
                    "模型调用轮数",
                    "neutral",
                ),
                (
                    "Token 总量",
                    str(summary.get("total_tokens", 0)),
                    "输入 + 输出",
                    "neutral",
                ),
                (
                    "缓存命中率",
                    f"{float(summary.get('cache_hit_rate') or 0):.2%}",
                    "模型侧缓存命中比例",
                    "ok",
                ),
                (
                    "估算成本",
                    f"${float(summary.get('estimated_cost_usd') or 0):.6f}",
                    "模型调用估算费用",
                    "ok",
                ),
                (
                    "模型延迟",
                    f"{int(summary.get('llm_latency_ms') or 0)} ms",
                    "模型调用总耗时",
                    "neutral",
                ),
                (
                    "失败工具调用",
                    str(summary.get("failed_tool_calls", 0)),
                    "执行失败的工具次数",
                    "bad" if summary.get("failed_tool_calls") else "ok",
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>自适应运行信号</h3><span>只显示本次真实观测</span></div>",
        "<table><thead><tr><th>能力</th><th>观测结果</th><th>约束边界</th><th>负责模块</th></tr></thead>",
        f"<tbody>{adaptive_rows}</tbody></table>",
        "<p class='boundary-note'>数值为 0 表示本次运行没有触发该能力，不代表能力验证通过。</p></section>",
        "<details class='drilldown'><summary>查看每轮 Token、成本与工具调用明细</summary>"
        "<div class='drilldown-body'><h3>每轮成本明细</h3>",
        "<table><thead><tr><th>轮次</th><th>模型调用</th><th>输入 Token</th><th>输出 Token</th><th>成本</th><th>上下文字符数</th><th>动作</th></tr></thead>"
        f"<tbody>{step_rows_html}</tbody></table>",
        "<div class='split'>",
        "<div><h3>上下文组成</h3><table><thead><tr><th>区段</th><th>字符数</th></tr></thead>"
        f"<tbody>{context_rows_html}</tbody></table></div>",
        "<div><h3>工具效率</h3><table><thead><tr><th>工具</th><th>调用</th><th>成功</th><th>失败</th><th>耗时（ms）</th></tr></thead>"
        f"<tbody>{tool_rows_html}</tbody></table></div>",
        "</div></div></details>",
        f"<details class='provenance'><summary>用量证据来源</summary><code>{_escape(str(usage_path))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_fanout_task_contract(
    fanout: dict[str, Any],
    summary_path: Path | None,
) -> str:
    """对照计划约束与 Worker 事实，解释为什么可并发以及实际做了什么。"""

    plan_path = summary_path.parent / "fanout_plan.json" if summary_path else None
    plan = _read_json_file(plan_path)
    task_specs = [task for task in plan.get("tasks") or [] if isinstance(task, dict)]
    results = [
        result for result in fanout.get("results") or [] if isinstance(result, dict)
    ]
    result_by_task = {str(result.get("task_id") or ""): result for result in results}
    rows = []
    for task in task_specs:
        task_id = str(task.get("id") or "unknown")
        result = result_by_task.get(task_id, {})
        usage = result.get("usage_summary") or {}
        dependencies = _string_items(task.get("depends_on"))
        dependency_facts = dependencies or ["无前置依赖：允许与同批次任务并行"]
        outcome_facts = [
            f"状态：{_display_value(result.get('status') or 'not_observed')}",
            f"工具调用：{int(usage.get('tool_calls') or 0)} 次",
            f"失败调用：{int(usage.get('failed_tool_calls') or 0)} 次",
        ]
        rows.append(
            "<tr>"
            f"<td><b>{_escape(task_id)}</b><br><span class='table-note'>{_escape(task.get('task') or '')}</span></td>"
            f"<td>{_render_fact_list(dependency_facts, empty_message='无')}</td>"
            f"<td>{_render_fact_list(task.get('write_scope'), empty_message='未声明写入范围')}</td>"
            f"<td>{_render_fact_list(task.get('allowed_tools'), empty_message='未声明工具白名单')}</td>"
            f"<td>{_render_fact_list(result.get('touched_files'), empty_message='没有改动文件')}</td>"
            f"<td>{_render_fact_list(outcome_facts, empty_message='未产生结果')}</td>"
            "</tr>"
        )
    rows_html = (
        "".join(rows)
        or "<tr><td colspan='6'>本次 Fanout 没有可读取的任务计划。</td></tr>"
    )
    return (
        "<section class='evidence-section'><div class='section-title'><h3>任务契约与真实结果</h3>"
        "<span>先限定依赖、写入范围和工具，再核对实际改动</span></div>"
        "<table><thead><tr><th>任务</th><th>依赖关系</th><th>允许写入</th><th>允许工具</th><th>实际改动</th><th>执行结果</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
        "<p class='boundary-note'>Worker 只有在依赖满足且写入范围不重叠时才进入同一并发批次；合并后由独立 Finalizer 做只读验证。</p></section>"
    )


def _render_fanout_scenario_contract(summary_path: Path | None) -> str:
    """展示 Debug Lab 可选业务矩阵；普通 Fanout 没有该文件时保持通用页面。"""

    contract_path = (
        summary_path.parent.parent / "scenario_contract.json"
        if summary_path is not None
        else None
    )
    contract = _read_json_file(contract_path)
    if contract.get("artifact_type") != "debug_lab_scenario_contract":
        return ""
    rows = []
    for item in contract.get("cases") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td><b>{_escape(item.get('case') or '-')}</b></td>"
            f"<td>{_escape(item.get('expected') or '-')}</td>"
            f"<td>{_escape(item.get('owner') or '-')}</td>"
            "</tr>"
        )
    if not rows:
        return ""
    return (
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>正常路径与异常分支</h3><span>先分配责任，再执行依赖验证</span></div>"
        "<table><thead><tr><th>业务场景</th><th>期望行为</th><th>责任任务</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        f"<p class='boundary-note'><strong>依赖门：</strong>{_escape(contract.get('integration_gate') or '-')}</p>"
        f"<details class='provenance'><summary>场景契约来源</summary><code>{_escape(str(contract_path))}</code></details>"
        "</section>"
    )


def _render_fanout_result_summary(fanout: dict[str, Any], path: Path | None) -> str:
    metrics = fanout.get("metrics") or {}
    results = [
        result for result in fanout.get("results") or [] if isinstance(result, dict)
    ]
    batches = fanout.get("batches") or []
    conflicts = fanout.get("conflicts") or []
    task_cards = []
    for result in results:
        touched_files = ", ".join(
            str(item) for item in result.get("touched_files") or []
        )
        final_answer = " ".join(str(result.get("final_answer") or "").split())
        task_cards.append(
            "<article class='worker-card'>"
            "<div class='artifact-head'><div>"
            f"<span>Worker · 批次 {int(result.get('batch_index') or 0) + 1}</span>"
            f"<h4>{_escape(result.get('task_id') or '未命名任务')}</h4></div>"
            f"{_badge(str(result.get('status') or 'unknown'), _tone_for_status(str(result.get('status') or '')))}</div>"
            f"<p><b>改动范围：</b>{_escape(touched_files or '没有文件改动')}</p>"
            f"<div class='worker-facts'><span>{int(result.get('duration_ms') or 0)} ms</span>"
            f"<span>{'恢复执行' if result.get('resumed') else '本次新执行'}</span></div>"
            "<details><summary>查看 Worker 结论与证据路径</summary>"
            f"<p>{_escape(final_answer or '没有可展示的结论摘要。')}</p>"
            f"<code>{_escape(result.get('trace_path') or '未找到 Trace')}</code>"
            f"<code>{_escape(result.get('candidate_diff_path') or '未生成候选改动')}</code>"
            "</details></article>"
        )
    task_content = (
        "".join(task_cards)
        if task_cards
        else "<div class='empty-inline'>没有 Worker 结果。</div>"
    )
    batch_text = (
        "；".join(
            f"批次 {index}: {', '.join(str(task) for task in batch)}"
            for index, batch in enumerate(batches, start=1)
        )
        or "未记录"
    )
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>并行多 Agent</span>"
        "<h2>并行任务与最终收口</h2></div><div class='view-heading-actions'>"
        "<button onclick=\"loadEvidence('timeline')\">查看本次执行过程</button>"
        f"{_badge(str(fanout.get('status') or 'unknown'), _tone_for_status(str(fanout.get('status') or '')))}</div></div>",
        _render_lab_brief(
            question=(
                "两个写入范围不重叠的策略修复能否并发执行，并让依赖它们的异常"
                "分支验证在合并后运行，最后再由独立 Finalizer 收口？"
            ),
            input_label="本次总任务",
            input_items=[str(fanout.get("goal") or "未记录总体目标")],
            mechanism=(
                "依赖与写入范围校验 → 隔离 Worker → 改动范围门禁 → "
                "确定性合并 → 只读 Finalizer"
            ),
            success_criteria=(
                f"{metrics.get('completed_count', 0)}/{metrics.get('task_count', 0)} "
                f"个任务完成，冲突 {len(conflicts)} 个，最终决策 "
                f"{_display_value(fanout.get('final_decision') or 'not_run')}。"
            ),
            boundary=(
                "本次可复现运行使用确定性 Worker 模型验证编排与隔离机制，不调用外部大模型；"
                "本地 Finalizer 通过也不等于官方 Benchmark 已解决。"
            ),
        ),
        _render_fanout_scenario_contract(path),
        _metric_grid(
            [
                (
                    "协调状态",
                    _display_value(fanout.get("status", "")),
                    "协调器最终状态",
                    _tone_for_status(str(fanout.get("status", ""))),
                ),
                (
                    "任务完成",
                    f"{metrics.get('completed_count', 0)}/{metrics.get('task_count', 0)}",
                    "通过依赖与范围校验",
                    "ok",
                ),
                (
                    "并发批次",
                    str(len(batches)),
                    f"最大并发 {metrics.get('max_workers', 0)}",
                    "neutral",
                ),
                (
                    "改动冲突",
                    str(len(conflicts)),
                    "检测到的文件范围冲突",
                    "bad" if conflicts else "ok",
                ),
                (
                    "端到端耗时",
                    _format_milliseconds(int(metrics.get("wall_time_ms") or 0)),
                    "包含合并与最终验证",
                    "neutral",
                ),
                (
                    "最终决策",
                    _display_value(fanout.get("final_decision") or "not_run"),
                    "隔离验证器结论",
                    _tone_for_status(str(fanout.get("final_decision") or "")),
                ),
            ]
        ),
        _render_fanout_task_contract(fanout, path),
        "<section class='evidence-section'><div class='section-title'><h3>为什么允许并行</h3>"
        "<span>依赖和改动范围先于并发</span></div>"
        "<div class='answer-strip'>"
        f"<div><b>依赖批次</b><span>{_escape(batch_text)}</span></div>"
        f"<div><b>范围冲突</b><span>{'发现 ' + str(len(conflicts)) + ' 项' if conflicts else '未发现重叠写入'}</span></div>"
        f"<div><b>最终收口</b><span>{_escape(_display_value(fanout.get('final_decision') or 'not_run'))}</span></div>"
        "</div></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Worker 执行结果</h3>"
        "<span>先看任务和改动范围，路径按需展开</span></div>"
        f"<div class='worker-grid'>{task_content}</div></section>",
        "<p class='boundary-note'>最终验证通过只证明这次合并后的本地检查通过，不等于官方 Benchmark 已解决。</p>",
        "<details class='drilldown'><summary>查看并发成本与恢复统计</summary>"
        "<div class='drilldown-body'>"
        f"{_metric_grid([('模型调用', str(metrics.get('llm_calls', 0)), 'Worker 与最终验证器', 'neutral'), ('工具调用', str(metrics.get('tool_calls', 0)), '全部执行链', 'neutral'), ('失败工具调用', str(metrics.get('failed_tool_calls', 0)), '实际失败观察', 'bad' if metrics.get('failed_tool_calls') else 'ok'), ('恢复任务', str(metrics.get('resumed_count', 0)), '复用历史 Worker 结果', 'neutral')])}"
        "</div></details>",
        f"<details class='provenance'><summary>多 Agent 证据来源</summary><code>{_escape(str(path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_fanout_usage_dashboard(fanout: dict[str, Any], path: Path | None) -> str:
    metrics = fanout.get("metrics") or {}
    body = [
        "<h2>Live Fanout 成本与并发证据</h2>",
        "<p class='help strong'>总模型指标包含 workers 与 finalizer；worker time 和 wall time 分开显示。</p>",
        _metric_grid(
            [
                ("模型调用", str(metrics.get("llm_calls", 0)), "本次运行", "neutral"),
                (
                    "Token 总量",
                    str(metrics.get("total_tokens", 0)),
                    "本次运行",
                    "neutral",
                ),
                (
                    "估算成本",
                    f"${float(metrics.get('estimated_cost_usd') or 0):.6f}",
                    "本次运行估算",
                    "ok",
                ),
                (
                    "总耗时",
                    f"{int(metrics.get('wall_time_ms') or 0)} ms",
                    "端到端耗时",
                    "neutral",
                ),
                (
                    "本次 Worker 耗时",
                    f"{int(metrics.get('current_worker_duration_ms') or 0)} ms",
                    "仅本次执行",
                    "neutral",
                ),
                (
                    "恢复 Worker 耗时",
                    f"{int(metrics.get('resumed_worker_duration_ms') or 0)} ms",
                    "来自历史产物",
                    "neutral",
                ),
                (
                    "最大并发数",
                    str(metrics.get("max_workers", 0)),
                    "配置的并发上限",
                    "neutral",
                ),
                (
                    "工具调用",
                    str(metrics.get("tool_calls", 0)),
                    "Worker 与验证器总和",
                    "neutral",
                ),
                (
                    "失败工具调用",
                    str(metrics.get("failed_tool_calls", 0)),
                    "失败观测数量",
                    "bad" if metrics.get("failed_tool_calls") else "ok",
                ),
            ]
        ),
        f"<p><span class='label'>fanout_summary.json</span><span class='mono'>{_escape(str(path or '未找到'))}</span></p>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


# 主要入口：把底层 Trace 投影成“运行级事件 + 每轮四段主链”。
def _render_trace_timeline(project_dir: Path) -> str:
    trace_entries = _trace_paths_for_timeline(project_dir)
    if not trace_entries:
        return _empty_evidence("没有找到 trace.json，请先运行 Agent 或一个 Debug Lab。")
    return _render_trace_timeline_entries(
        trace_entries,
        scope_label="受治理单 Agent",
        title="AgentLoop 执行时间线",
    )


def _render_orchestration_trace_timeline(project_dir: Path) -> str:
    """只展示 Lab 2 当前 Fanout 的 Worker 与 Finalizer Trace。"""

    fanout_path = _latest_orchestration_fanout_path(project_dir)
    fanout = _read_json_file(fanout_path)
    trace_entries: list[tuple[str, Path]] = []
    for result in fanout.get("results") or []:
        if not isinstance(result, dict):
            continue
        trace_path = Path(str(result.get("trace_path") or ""))
        if trace_path.is_file():
            task_id = str(result.get("task_id") or "未命名任务")
            trace_entries.append((f"Worker · {task_id}", trace_path))
    finalizer_path = Path(str(fanout.get("finalizer_trace_path") or ""))
    if finalizer_path.is_file():
        trace_entries.append(("Finalizer · 合并后验证", finalizer_path))
    if not trace_entries:
        return _empty_evidence(
            "并行多 Agent 尚未产生 Worker/Finalizer Trace，请先执行该运行。"
        )
    return _render_trace_timeline_entries(
        trace_entries,
        scope_label="并行多 Agent",
        title="Worker 与 Finalizer 时间线",
        source_path=fanout_path,
    )


def _render_complex_trace_timeline(project_dir: Path) -> str:
    """只展示 Lab 3 本次真实模型运行，避免混入其他 Lab 的 Trace。"""

    trace_path = _latest_complex_trace_path(project_dir)
    if trace_path is None:
        return _empty_evidence(
            "复杂真实修复尚未产生模型 Trace，请先运行 PyCharm 的 "
            "NanoHarness Lab 3 - Complex Live Repair。"
        )
    return _render_trace_timeline_entries(
        [("复杂结算修复 AgentLoop", trace_path)],
        scope_label="复杂真实修复",
        title="多轮检索、修改与验证时间线",
        source_path=trace_path,
    )


def _render_complex_context_inspector(project_dir: Path) -> str:
    """把 Lab 3 的长 Context 投影为逐轮可检查的输入与决定。"""

    trace_path = _latest_complex_trace_path(project_dir)
    if trace_path is None:
        return _empty_evidence(
            "复杂真实修复尚未发布 Context Evidence。完成运行并退出 Operator Console 后，"
            "这里会读取该次 Trace；不会要求重新调用模型。"
        )
    trace = _read_json_file(trace_path)
    turns = build_context_turn_inspections(trace)
    if not turns:
        return _empty_evidence("当前 Trace 没有可投影的 AgentLoop Turn。")

    key_turns = [turn for turn in turns if turn.is_key_turn]
    unique_tools = sorted(
        {decision.tool_name for turn in turns for decision in turn.tool_decisions}
    )
    peak_tokens = max((turn.estimated_tokens for turn in turns), default=0)
    compacted_turns = sum(turn.compacted for turn in turns)
    key_turn_links = "".join(
        "<a class='context-jump' "
        f"href='#context-turn-{turn.step}'><b>Turn {turn.step}</b>"
        f"<span>{_escape(turn.key_reason)}</span></a>"
        for turn in key_turns
    )
    turn_blocks = "".join(_render_context_turn(turn) for turn in turns)
    task = str(trace.get("task") or "未记录任务")

    return (
        "<div class='evidence'>"
        "<div class='view-heading'><div><span class='view-kicker'>CONTEXT LENS</span>"
        "<h2>上下文与决策观察器</h2></div>"
        "<span class='claim-note'>真实输入形状，不是隐藏思维链</span></div>"
        "<p class='help strong'>这里回答四个问题：上一轮新增了什么事实、本轮给模型看了什么、"
        "模型选择了什么动作、执行结果怎样进入下一轮。完整 Prompt 不重复落盘，页面只使用"
        "可审计的 Trace 来源、长度、摘要和工具结果。</p>"
        + _render_lab_brief(
            question="AgentLoop 为什么在这一轮作出当前工具选择，下一轮又为什么改变方向？",
            input_label="本次 Task",
            input_items=[task],
            mechanism=(
                "按 Turn 关联 context_assembly、context_window、llm_call、action 与 "
                "tool_observation；比较相邻轮的消息数、Token 和新增 Evidence。"
            ),
            success_criteria=(
                "不用阅读几千 Token 原文，也能指出关键转折、输入来源、模型动作和反馈闭环。"
            ),
            boundary=(
                "模型内部推理不可观测；llm_response_summary 只代表模型显式输出。"
                "阶段标签由 Workbench 根据工具与结果归类，不冒充模型思维。"
            ),
        )
        + _metric_grid(
            [
                ("Agent Turn", str(len(turns)), "本次真实模型边界", "neutral"),
                ("关键转折", str(len(key_turns)), "建议优先展开", "ok"),
                ("峰值输入", f"{peak_tokens:,} tokens", "压缩后的估算输入", "neutral"),
                (
                    "上下文压缩",
                    str(compacted_turns),
                    "发生压缩的 Turn 数",
                    "warn" if compacted_turns else "neutral",
                ),
                (
                    "实际工具",
                    str(len(unique_tools)),
                    "本次真正调用过的工具种类",
                    "neutral",
                ),
            ]
        )
        + "<section class='evidence-section'><div class='section-title'>"
        "<h3>关键轮次导航</h3><span>优先检查转折，再按需展开其他轮次</span></div>"
        f"<div class='context-jumps'>{key_turn_links}</div></section>"
        "<section class='evidence-section'><div class='section-title'>"
        "<h3>每轮检查结构</h3><span>按四个阶段展示，不重复原始消息数组</span></div>"
        "<div class='mini-flow context-mental-model'>"
        "<span><b>1 新增证据</b><small>上一轮 Observation</small></span>"
        "<span><b>2 输入组成</b><small>系统上下文 + 历史 + Tool Schema</small></span>"
        "<span><b>3 模型决定</b><small>显式回答或 ToolCall</small></span>"
        "<span><b>4 执行反馈</b><small>结果写回下一轮</small></span>"
        "</div></section>"
        "<section class='evidence-section context-turns'><div class='section-title'>"
        "<h3>逐轮观察</h3><span>关键转折默认展开</span></div>"
        f"{turn_blocks}</section>"
        "<details class='provenance'><summary>本页证据来源与边界</summary>"
        f"<code>{_escape(str(trace_path))}</code>"
        "<p>页面没有重新调用模型，也没有用 LLM 二次总结；所有标签由确定性投影生成。</p>"
        "</details></div>"
    )


def _render_context_turn(
    turn: ContextTurnInspection,
    *,
    element_id: str | None = None,
) -> str:
    """渲染一轮四段式 Context 检查视图。"""

    open_attribute = " open" if turn.is_key_turn else ""
    key_badge = (
        f"<span class='context-key'>{_escape(turn.key_reason)}</span>"
        if turn.is_key_turn
        else ""
    )
    previous_evidence = _render_context_fact_list(turn.previous_evidence)
    decision_summary = _compact_timeline_text(
        turn.model_response_summary or "模型只返回了结构化 ToolCall。",
        max_chars=520,
    )
    action_rows = _render_context_action_list(turn.tool_decisions)
    feedback_rows = _render_context_feedback_list(turn.tool_decisions)
    input_bars = _render_context_component_bars(turn.input_components)
    message_delta = _signed_number(turn.message_delta)
    token_delta = _signed_number(turn.token_delta)
    pressure = (
        turn.estimated_tokens / turn.hard_input_limit * 100
        if turn.hard_input_limit
        else 0.0
    )
    technical_details = _render_context_technical_details(turn)

    resolved_element_id = element_id or f"context-turn-{turn.step}"
    return (
        f"<details class='context-turn' id='{_escape(resolved_element_id)}'{open_attribute}>"
        "<summary>"
        f"<span><b>Turn {turn.step}</b><small>{_escape(turn.phase)}</small></span>"
        f"<span class='context-turn-summary'>{_escape(turn.phase_reason)}</span>"
        f"<span class='context-turn-metrics'>{turn.message_count} messages "
        f"({message_delta}) · {turn.estimated_tokens:,} tokens ({token_delta})</span>"
        f"{key_badge}</summary>"
        "<div class='context-turn-body'>"
        "<div class='context-flow-grid'>"
        "<section><span class='context-stage'>01 · 上一轮新增证据</span>"
        f"{previous_evidence}</section>"
        "<section><span class='context-stage'>02 · 本轮输入组成</span>"
        f"{input_bars}"
        f"<p class='context-caption'>窗口占用约 {pressure:.1f}% · "
        f"{'已压缩' if turn.compacted else '未压缩'} · "
        f"系统 Context {turn.total_context_chars:,}/{turn.max_context_chars:,} chars</p>"
        "</section>"
        "<section><span class='context-stage'>03 · 模型可观测决定</span>"
        f"<p class='context-decision'>{_escape(decision_summary)}</p>{action_rows}</section>"
        "<section><span class='context-stage'>04 · 执行反馈</span>"
        f"{feedback_rows}<p class='context-caption'>这些 Observation 会进入下一 Turn。"
        "若本轮已经形成最终答案，则不再回填工具结果。</p></section>"
        "</div>"
        f"{technical_details}</div></details>"
    )


def _render_context_component_bars(
    components: tuple[ContextComponent, ...],
) -> str:
    labels = {
        "system_context": "系统上下文",
        "conversation_history": "对话与工具历史",
        "tool_schemas": "Tool Schema",
    }
    total = max(sum(component.chars for component in components), 1)
    rows = "".join(
        "<div class='context-bar-row'>"
        f"<span>{_escape(labels.get(component.key, component.key))}</span>"
        "<div class='context-bar-track'>"
        f"<i style='width:{max(component.chars / total * 100, 1.5):.1f}%'></i></div>"
        f"<b>{component.chars:,}</b></div>"
        for component in components
    )
    return f"<div class='context-bars'>{rows}</div>"


def _render_context_action_list(decisions: tuple[ToolDecision, ...]) -> str:
    if not decisions:
        return "<p class='empty-inline'>没有 ToolCall，本轮输出最终文本。</p>"
    rows = "".join(
        "<li><b class='mono'>"
        f"{_escape(decision.tool_name)}</b><span>{_escape(decision.target)}</span></li>"
        for decision in decisions
    )
    return f"<ol class='context-actions'>{rows}</ol>"


def _render_context_feedback_list(decisions: tuple[ToolDecision, ...]) -> str:
    if not decisions:
        return "<p class='empty-inline'>本轮没有工具执行反馈。</p>"
    rows = "".join(
        "<li>"
        + _badge(
            "成功"
            if decision.succeeded is True
            else "未通过"
            if decision.succeeded is False
            else "未执行",
            "ok"
            if decision.succeeded is True
            else "bad"
            if decision.succeeded is False
            else "neutral",
        )
        + f"<span>{_escape(decision.feedback)}</span></li>"
        for decision in decisions
    )
    return f"<ul class='context-feedback'>{rows}</ul>"


def _render_context_technical_details(turn: ContextTurnInspection) -> str:
    system_rows = (
        "".join(
            f"<tr><td>{_escape(_display_context_section(component.key))}</td>"
            f"<td>{component.chars:,}</td></tr>"
            for component in turn.system_sections
        )
        or "<tr><td colspan='2'>没有区段数据</td></tr>"
    )
    tools_state = "发生变化" if turn.tools_changed else "与上一轮相同"
    skills_state = "发生变化" if turn.skills_changed else "与上一轮相同"
    files_seen = "、".join(turn.files_seen) or "尚未通过 read_file 取得文件正文"
    selected_files = (
        "、".join(turn.selected_files) or "0（本次文件正文来自工具 Observation）"
    )
    visible_tools = "、".join(turn.visible_tools) or "无"
    active_skills = "、".join(turn.active_skills) or "无"
    dropped_tools = "、".join(turn.dropped_tools) or "无"
    working_memory = turn.working_memory_summary or "未记录"

    return (
        "<details class='context-technical'><summary>查看这一轮怎样组装 Context</summary>"
        "<div class='context-technical-grid'>"
        "<div><h4>来源与可见性</h4><table><tbody>"
        f"<tr><td>历史已读文件</td><td>{_escape(files_seen)}</td></tr>"
        f"<tr><td>主动选择文件</td><td>{_escape(selected_files)}</td></tr>"
        f"<tr><td>可见工具</td><td>{_escape(visible_tools)}（{_escape(tools_state)}）</td></tr>"
        f"<tr><td>隐藏工具</td><td>{_escape(dropped_tools)}</td></tr>"
        f"<tr><td>激活 Skill</td><td>{_escape(active_skills)}（{_escape(skills_state)}）</td></tr>"
        "</tbody></table></div>"
        "<div><h4>系统 Context 内部预算</h4><table><thead>"
        f"<tr><th>区段</th><th>字符数</th></tr></thead><tbody>{system_rows}</tbody></table></div>"
        "</div>"
        "<details class='context-memory'><summary>查看 Working Memory 摘要</summary>"
        f"<pre>{_escape(working_memory)}</pre></details>"
        f"<p class='boundary-note'>模型：{_escape(turn.model_name)}；reasoning tokens："
        f"{turn.reasoning_tokens}；本轮估算成本：${turn.estimated_cost_usd:.6f}；"
        f"压缩原因：{_escape(turn.compaction_reason)}。Trace 不复制完整 Prompt，"
        "避免重复大文本和敏感内容；这里展示的是可复核的输入结构。</p>"
        "</details>"
    )


def _render_context_fact_list(values: tuple[str, ...]) -> str:
    rows = "".join(f"<li>{_escape(value)}</li>" for value in values)
    return f"<ul class='context-facts'>{rows}</ul>"


def _signed_number(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _render_trace_timeline_entries(
    trace_entries: list[tuple[str, Path]],
    *,
    scope_label: str,
    title: str,
    source_path: Path | None = None,
) -> str:
    """先展示 AgentLoop 时间线，再允许按 ToolCall 四层下钻 Trace。"""

    body = [
        f"<div class='view-heading'><div><span class='view-kicker'>{_escape(scope_label)}</span>"
        f"<h2>{_escape(title)}</h2></div>"
        "<span class='claim-note'>AgentLoop 主链与 ToolCall 四层明细</span></div>",
        "<p class='help strong'>时间线只回答一轮发生了什么。出现 ToolCall 时，统一按"
        "“入口控制 → 执行决策 → 受限执行 → 结果与恢复”理解；具体 Hook、权限、操作状态表"
        "和 Trace 事件作为各阶段的实现与审计证据按需展开。</p>",
        "<div class='mini-flow timeline-mental-model'>"
        "<span><b>1 准备模型输入</b><small>组装上下文与本轮工具范围</small></span>"
        "<span><b>2 模型提出意图</b><small>回答问题或提出 ToolCall</small></span>"
        "<span><b>3 Runtime 处理意图</b><small>入口控制、执行决策与受限执行</small></span>"
        "<span><b>4 结果回填</b><small>Observation、证据与恢复状态</small></span>"
        "</div>",
        "<p class='boundary-note'>没有 ToolCall 时，Runtime 不进入工具治理；需要定位具体机制时，"
        "再展开对应阶段的底层证据和代码 owner。</p>",
    ]
    for label, trace_path in trace_entries:
        body.append(_render_trace_lane(label, trace_path))
    if source_path is not None:
        body.append(
            "<details class='provenance'><summary>本页证据作用域</summary>"
            f"<code>{_escape(str(source_path))}</code></details>"
        )
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_complex_lab_dashboard(project_dir: Path) -> str:
    """展示真实模型复杂任务的目标、过程、结果与证据边界。"""

    run_dir = _latest_complex_run_dir(project_dir)
    if run_dir is None:
        return _empty_evidence(
            "还没有复杂任务 Evidence。运行 PyCharm 的 "
            "NanoHarness Lab 3 - Complex Live Repair；完成或主动退出 TUI 后，"
            "这里会固定展示那一次运行。"
        )

    story: RunStory | None = None
    story_error = ""
    try:
        story = _latest_complex_run_story(project_dir)
    except (OSError, ValueError) as exc:
        story_error = str(exc)
    trace_path = _latest_complex_trace_path(project_dir)
    usage_path = _latest_complex_usage_path(project_dir)
    trace = _read_json_file(trace_path)
    usage = _read_json_file(usage_path)
    practice_profile = _read_json_file(run_dir / "practice_profile.json")
    auto_approve_writes = practice_profile.get("auto_approve_writes") is True
    max_steps = int(practice_profile.get("max_steps") or 24)
    write_approval_label = "隔离区内自动批准" if auto_approve_writes else "逐项人工审批"
    write_approval_boundary = (
        "普通写操作不中断；路径、命令与网络边界仍执行"
        if auto_approve_writes
        else "每个具体写操作绑定 Operation Key 与目标 Fingerprint"
    )
    usage_summary = usage.get("summary") or {}
    events = _event_list(trace)
    turns = {
        int(event.get("step") or 0)
        for event in events
        if int(event.get("step") or 0) > 0
    }
    task = (
        story.task
        if story is not None and story.task
        else str(usage.get("task") or trace.get("task") or "未记录任务")
    )
    status = (
        story.status
        if story is not None
        else str(
            usage_summary.get("latest_task_status")
            or trace.get("stop_reason")
            or "unknown"
        )
    )
    checkpoint_count = sum(
        event.get("event_type") == "task_state_checkpoint" for event in events
    )
    approval_events = sum(
        event.get("event_type") in {"human_approval", "human_input_requested"}
        for event in events
    )
    candidate_diff_bytes = 0
    if story is not None:
        candidate_diff = next(
            (
                artifact
                for artifact in story.artifacts
                if artifact.kind == "candidate_diff"
            ),
            None,
        )
        candidate_diff_bytes = candidate_diff.byte_size if candidate_diff else 0

    validation_events = [
        event for event in events if event.get("event_type") == "validation_evidence"
    ]
    validation_rows = (
        "".join(
            _render_complex_validation_row(index, event)
            for index, event in enumerate(validation_events, start=1)
        )
        or "<tr><td colspan='4'>本次运行尚未留下 focused/full pytest 证据。</td></tr>"
    )

    body = [
        "<div class='view-heading'><div><span class='view-kicker'>真实 DEEPSEEK 运行</span>"
        "<h2>复杂结算修复运行场景</h2></div><div class='view-heading-actions'>"
        "<button onclick=\"loadEvidence('context')\">查看上下文与决策</button>"
        f"{_badge(status, _tone_for_status(status))}</div></div>",
        _render_lab_brief(
            question=(
                "面对同时包含幂等、金额舍入、部分结算和失败原子性的多模块缺陷，"
                "Agent 能否通过多轮检索、修改、失败反馈和回归验证收敛？"
            ),
            input_label="本次 Task",
            input_items=[task],
            mechanism=(
                f"真实 DeepSeek + {max_steps} 轮有界 AgentLoop；写操作{write_approval_label}；"
                "先跑 focused tests，"
                "再跑完整回归；每轮工具、观察、Checkpoint 和用量都进入 Trace。"
            ),
            success_criteria=(
                "不改测试；focused 与完整 pytest 都通过；生成非空 candidate diff；"
                "最终回答基于实际验证证据。"
            ),
            boundary=(
                "这是本地可控工程场景，不是 SWE-bench official 结果；模型每次路径和轮数可变，"
                "失败、暂停或未收敛同样会留下可诊断的运行证据。"
            ),
        ),
        _metric_grid(
            [
                (
                    "运行模式",
                    str(practice_profile.get("title") or "未记录"),
                    str(practice_profile.get("purpose") or "真实模型复杂任务"),
                    "neutral",
                ),
                (
                    "写操作策略",
                    write_approval_label,
                    write_approval_boundary,
                    "warn" if not auto_approve_writes else "ok",
                ),
                (
                    "运行状态",
                    _display_value(status),
                    str(
                        story.stop_reason
                        if story is not None
                        else trace.get("stop_reason") or "-"
                    ),
                    _tone_for_status(status),
                ),
                (
                    "Agent 轮次",
                    str(len(turns)),
                    f"上限 {max_steps} 轮；实际模型调用 {int(usage_summary.get('llm_calls') or 0)} 次",
                    "neutral",
                ),
                (
                    "工具调用",
                    str(int(usage_summary.get("tool_calls") or 0)),
                    (
                        f"执行故障 {int(usage_summary.get('failed_tool_calls') or 0)} 次；"
                        f"验证未通过 {int(usage_summary.get('failed_validations') or 0)} 次"
                    ),
                    "bad" if usage_summary.get("failed_tool_calls") else "ok",
                ),
                (
                    "验证证据",
                    str(len(validation_events)),
                    "focused / full pytest 的实际结果",
                    "ok" if validation_events else "warn",
                ),
                (
                    "上下文压缩",
                    str(int(usage_summary.get("compacted_context_turns") or 0)),
                    f"截断 {int(usage_summary.get('truncated_context_steps') or 0)} 轮 · 溢出恢复 {int(usage_summary.get('context_overflow_recoveries') or 0)} 次",
                    "warn"
                    if usage_summary.get("compacted_context_turns")
                    else "neutral",
                ),
                (
                    "Checkpoint / 人工介入",
                    f"{checkpoint_count} / {approval_events}",
                    "状态写入次数 / Trace 中的人工事件",
                    "neutral",
                ),
                (
                    "Token / 估算成本",
                    f"{int(usage_summary.get('total_tokens') or 0)} / ${float(usage_summary.get('estimated_cost_usd') or 0.0):.4f}",
                    f"候选改动 {candidate_diff_bytes} 字节",
                    "neutral",
                ),
            ]
        ),
        (
            "<section class='evidence-section'><div class='section-title'>"
            "<h3>本次人工控制动作</h3><span>用于验证控制面行为</span></div>"
            + _render_fact_list(
                practice_profile.get("operator_drill"),
                empty_message="本模式先观察，不要求主动干预。",
            )
            + "</section>"
        ),
        "<section class='evidence-section'>"
        "<div class='section-title'><h3>为什么它不是一步修复</h3>"
        "<span>先暴露局部问题，再由完整回归揭示跨模块不变量</span></div>"
        "<div class='pipeline'>"
        "<div><b>01</b><span>仓库定向</span><small>识别 domain、repository、service 与两组测试</small></div>"
        "<div><b>02</b><span>Focused 失败</span><small>幂等键规范化、金额舍入、partial 状态</small></div>"
        "<div><b>03</b><span>局部修复</span><small>修改后重新运行 focused tests</small></div>"
        "<div><b>04</b><span>完整回归</span><small>额外暴露失败原子性与可重试边界</small></div>"
        "<div><b>05</b><span>再次修正</span><small>校验必须先于 ledger 和幂等状态写入</small></div>"
        "<div><b>06</b><span>结果收口</span><small>全量通过、检查 Diff、再形成最终回答</small></div>"
        "</div><p class='boundary-note'>上面是任务的验证漏斗，不伪造模型必然采用的思考顺序；"
        "实际走了哪些分支，以时间线和验证证据为准。</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>实际验证记录</h3>"
        "<span>命令、状态与结果来自 Trace</span></div>"
        "<table><thead><tr><th>#</th><th>验证类型</th><th>状态</th><th>实际命令 / 结果摘要</th>"
        f"</tr></thead><tbody>{validation_rows}</tbody></table></section>",
        _render_run_story_section(story, run_dir, story_error),
        "<details class='provenance'><summary>本次复杂任务的固定证据来源</summary>"
        f"<code>{_escape(str(run_dir))}</code>"
        f"<code>{_escape(str(trace_path or '未找到 trace.json'))}</code>"
        f"<code>{_escape(str(usage_path or '未找到 usage.json'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_complex_validation_row(index: int, event: dict[str, Any]) -> str:
    """把 validation_evidence 压缩成能直接复核的一行。"""

    validation = event.get("validation") or {}
    if not isinstance(validation, dict):
        validation = {}
    kind = str(validation.get("kind") or "unknown")
    status = str(
        validation.get("status") or ("passed" if event.get("success") else "failed")
    )
    evidence = str(validation.get("evidence") or "")
    evidence_lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    summary = " · ".join(evidence_lines[:3]) or "没有记录命令摘要"
    return (
        "<tr>"
        f"<td>{index}</td><td>{_escape(kind)}</td>"
        f"<td>{_badge(status, _tone_for_status(status))}</td>"
        f"<td>{_escape(_compact_timeline_text(summary, max_chars=320))}</td>"
        "</tr>"
    )


def _render_trace_lane(label: str, trace_path: Path) -> str:
    trace = _read_json_file(trace_path)
    # Checkpoint 在 Trace 中保存完整快照；先与上一快照做差分，展示时才能解释
    # “为什么这一轮写了多次”，而不是把不同状态转换都叫作 Checkpoint。
    events = _annotate_pending_tool_rejections(
        _annotate_checkpoint_transitions(_event_list(trace))
    )
    run_events = [event for event in events if int(event.get("step") or 0) == 0]
    turns: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        step = int(event.get("step") or 0)
        if step > 0:
            turns.setdefault(step, []).append(event)

    turn_blocks = "".join(
        _render_timeline_turn(turn, turn_events)
        for turn, turn_events in sorted(turns.items())
    )
    return (
        "<section class='evidence-section timeline-lane'>"
        f"<div class='section-title'><h3>{_escape(label)}</h3>{_badge(str(trace.get('stop_reason') or 'unknown'), _tone_for_status(str(trace.get('stop_reason') or '')))}</div>"
        f"<div class='run-facts'><span>运行 <b class='mono'>{_escape(trace.get('run_id', ''))}</b></span>"
        f"<span>{len(turns)} 个 Agent 轮次</span><span>{len(run_events)} 个运行级事件</span>"
        f"<span>{len(events)} 条底层事件</span></div>"
        f"{_render_run_level_events(run_events)}{turn_blocks}"
        f"<details class='provenance'><summary>Trace 来源</summary><code>{_escape(str(trace_path))}</code></details>"
        "</section>"
    )


_TIMELINE_STAGES = (
    ("input", "01", "准备模型输入", "上下文、记忆、Skill 与工具范围", "purple"),
    ("decision", "02", "模型提出意图", "模型调用、回答或 ToolCall", "blue"),
    (
        "governed_execution",
        "03",
        "Runtime 处理意图",
        "入口控制、执行决策与受限执行",
        "warn",
    ),
    (
        "persistence",
        "04",
        "结果回填",
        "Observation、证据、恢复状态与停止原因",
        "neutral",
    ),
)

_TRACE_STAGE_BY_EVENT = {
    "turn_started": "input",
    "context_assembly": "input",
    "context_window": "input",
    "context_overflow_recovery": "input",
    "memory_recall": "input",
    "skill_selection": "input",
    "model_capabilities": "input",
    "model_started": "decision",
    "llm_call": "decision",
    "action": "decision",
    "clarification_decision": "decision",
    "final_answer": "decision",
    "pending_tool_call_rejected": "decision",
    "review_decision": "decision",
    "verifier_result": "decision",
    "guardrail_check": "governed_execution",
    "hook_check": "governed_execution",
    "permission_check": "governed_execution",
    "human_approval": "governed_execution",
    "human_input_requested": "governed_execution",
    "human_input_response_loaded": "governed_execution",
    "human_input_cancelled": "governed_execution",
    "run_control": "governed_execution",
    "tool_calls_deferred_for_human_input": "governed_execution",
    "tool_execution_started": "governed_execution",
    "tool_call": "governed_execution",
    "tool_observation": "governed_execution",
    "validation_evidence": "governed_execution",
    "tool_calls_bounded": "governed_execution",
    "operation_ledger": "governed_execution",
    "task_state_checkpoint": "persistence",
    "observation": "persistence",
    "evidence_collected": "persistence",
    "recovery_decision": "persistence",
    "resume_state_loaded": "persistence",
    "stop_hooks": "persistence",
    "run_completed": "persistence",
    "execution_environment": "persistence",
    "error": "persistence",
}

_HOOK_STAGE_LABELS = {
    "before_model": "模型调用前处理器",
    "after_model": "模型返回后处理器",
    "before_tool": "工具执行前规则",
    "after_tool": "工具结果处理器",
    "on_checkpoint": "Checkpoint 落盘后通知",
    "on_stop": "运行结束前检查",
}

_TRACE_EVENT_LABELS = {
    "turn_started": "轮次开始",
    "task_state_checkpoint": "Checkpoint",
    "model_capabilities": "模型能力",
    "context_assembly": "上下文组装",
    "context_window": "上下文窗口",
    "context_overflow_recovery": "上下文恢复",
    "model_started": "模型请求",
    "llm_call": "模型响应",
    "guardrail_check": "轻量语义检查记录",
    "clarification_decision": "澄清判断",
    "skill_selection": "Skill 选择",
    "memory_recall": "记忆召回",
    "action": "工具意图",
    "file_write": "产物写入",
    "permission_check": "工具最终权限",
    "hook_check": "生命周期处理器事件",
    "human_approval": "人工审批",
    "human_input_requested": "等待人工输入",
    "human_input_response_loaded": "载入人工回答",
    "human_input_cancelled": "人工输入已取消",
    "tool_calls_deferred_for_human_input": "工具调用等待人工输入",
    "tool_execution_started": "工具开始",
    "tool_calls_bounded": "工具数量限流",
    "tool_call": "工具调用记录",
    "tool_observation": "工具结果",
    "validation_evidence": "验证证据",
    "observation": "会话回填",
    "evidence_collected": "证据记录",
    "operation_ledger": "操作状态表",
    "recovery_decision": "恢复判断",
    "resume_state_loaded": "恢复状态载入",
    "verifier_result": "验证结论",
    "review_decision": "审查结论",
    "final_answer": "最终回答",
    "pending_tool_call_rejected": "收口失败：工具请求未执行",
    "stop_hooks": "停止质量门",
    "run_completed": "运行结束",
    "run_control": "运行控制",
    "execution_environment": "执行环境",
    "error": "运行错误",
    "multi_agent_start": "多 Agent 开始",
    "handoff": "角色交接",
    "agent_stage_start": "角色开始",
    "agent_stage_end": "角色结束",
    "artifact_created": "产物写入",
    "multi_agent_done": "多 Agent 完成",
    "fanout_start": "并发编排开始",
    "fanout_batch_done": "并发批次完成",
    "fanout_done": "并发编排完成",
    "finalizer_error": "最终验证失败",
}

# 每条解释回答“没有这条证据时会看不清什么”，避免用统一空话填表。
_TRACE_EVENT_PURPOSES = {
    "turn_started": "建立本轮边界，把后续上下文、模型决定和工具结果归到同一轮。",
    "model_capabilities": "记录模型是否支持工具调用等能力，避免把能力缺失误判为 AgentLoop 故障。",
    "context_assembly": "记录本轮选入了哪些上下文来源，便于解释模型实际看到了什么。",
    "context_window": "记录最终输入规模和压缩情况，用来定位上下文超限或信息丢失。",
    "context_overflow_recovery": "对比压缩前后输入规模，证明上下文超限恢复确实缩小了窗口。",
    "model_started": "标记请求已经交给模型，并记录输入规模；可区分调用前失败与模型服务失败。",
    "llm_call": "记录模型响应形态、用量和归一化结果，把本轮决定与成本、延迟关联起来。",
    "guardrail_check": "记录输入、ToolCall 或最终声明的轻量语义检查；真实阻断仍由路由、授权和执行边界负责。",
    "clarification_decision": "记录任务信息是否足够，解释为什么继续执行或转入人工澄清。",
    "skill_selection": "记录本轮激活的 Skill，解释额外指令和工具偏好从哪里进入上下文。",
    "memory_recall": "记录召回或放弃了哪些记忆，解释历史信息是否影响本轮判断。",
    "action": "保存模型提出的工具意图及参数摘要，作为权限判断前的原始请求。",
    "file_write": "记录产物实际写入位置，便于从运行结论追溯到可检查文件。",
    "human_approval": "保存人工对高风险操作的决定，使恢复后的执行仍有可审计授权依据。",
    "human_input_requested": "记录 Agent 缺少的具体信息，并把运行切换到可恢复的等待状态。",
    "human_input_response_loaded": "证明恢复时载入了哪次人工回答，避免把旧输入注入新任务。",
    "human_input_cancelled": "记录人工取消及后续控制结果，防止把取消误报为正常完成。",
    "tool_calls_deferred_for_human_input": "冻结尚未执行的工具请求，等待人工信息后重新规划。",
    "tool_calls_bounded": "记录预算裁剪，解释为什么模型请求的部分工具没有进入执行链。",
    "observation": "把工具结果写回会话，明确下一轮模型判断能够看到的反馈。",
    "evidence_collected": "把可复核事实加入证据集合，供最终回答和报告引用。",
    "recovery_decision": "记录中断后的继续、重试或阻断理由，避免恢复策略成为隐式行为。",
    "resume_state_loaded": "证明恢复从哪个 Checkpoint 和待处理操作继续，而不是重新执行整项任务。",
    "verifier_result": "记录独立验证者结论，使实现完成与验证通过保持分离。",
    "review_decision": "记录审查者发现和处理意见，解释最终收口是否接受候选改动。",
    "final_answer": "记录 Agent 对外发布的最终结论及证据引用，作为本次运行的输出边界。",
    "pending_tool_call_rejected": (
        "最终轮已经关闭工具执行，但模型仍输出工具请求；记录并拒绝该请求，"
        "防止未执行动作被误报成最终答案。"
    ),
    "stop_hooks": "运行宣称完成前执行质量门，防止带待处理工具或缺失证据的结果被误报为完成。",
    "run_completed": "固定最终状态和停止原因，使调用方不必从最后一条模型消息猜测结果。",
    "run_control": "记录暂停、取消或 steer 信号如何改变运行，便于复盘人工控制路径。",
    "execution_environment": "记录实际工作区、隔离模式和网络边界，说明工具在哪个环境运行。",
    "error": "保留失败阶段和错误类型，使异常不会只剩一条笼统的运行失败。",
    "multi_agent_start": "记录多 Agent 计划和参与角色，建立后续任务证据的共同起点。",
    "handoff": "记录角色间交付的任务和产物，避免把协作误解为共享聊天记录。",
    "agent_stage_start": "标记角色职责开始，便于按实现、审查和验证阶段归因。",
    "agent_stage_end": "记录角色职责的结果和产物，供下一角色消费与复核。",
    "artifact_created": "登记新产物的生产者和位置，使跨 Agent 交付可以追溯。",
    "multi_agent_done": "记录协调器最终状态，区分单个 Worker 完成与整体任务完成。",
    "fanout_start": "记录依赖批次和并发边界，解释哪些任务为什么能够同时执行。",
    "fanout_batch_done": "记录一批 Worker 的结果和冲突检查，决定是否可以进入合并。",
    "fanout_done": "记录合并与最终验证结论，作为并发执行的整体收口。",
    "finalizer_error": "记录最终验证阶段失败，避免 Worker 完成被误报为整体通过。",
}


def _render_run_level_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return ""
    failures = sum(1 for event in events if not bool(event.get("success", True)))
    return (
        "<div class='timeline-run-level'>"
        "<div class='timeline-head'><div><strong>运行级阶段</strong>"
        "<small>初始化 / 发布，不计入 Agent 轮次</small></div>"
        f"{_badge('存在失败' if failures else '初始化完成', 'bad' if failures else 'ok')}</div>"
        f"{_render_raw_event_details(events, summary='查看运行级底层事件')}"
        "</div>"
    )


def _annotate_checkpoint_transitions(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """用相邻完整快照推导每次 Checkpoint 的业务原因。

    Runtime 保留完整 ``TaskCheckpoint`` 是为了可靠恢复；Workbench 在只读侧做差分，
    把同名写入解释成“轮次起点、审批屏障、恢复、工具结果或终态”。
    """

    annotated_events: list[dict[str, Any]] = []
    previous_state: dict[str, Any] | None = None
    checkpoint_sequence = 0
    for event in events:
        annotated_event = dict(event)
        if str(event.get("event_type") or "") == "task_state_checkpoint":
            current_state_value = event.get("task_state") or {}
            current_state = (
                dict(current_state_value)
                if isinstance(current_state_value, dict)
                else {}
            )
            checkpoint_sequence += 1
            annotated_event["_checkpoint_transition"] = _checkpoint_transition(
                previous_state,
                current_state,
                sequence=checkpoint_sequence,
            )
            previous_state = current_state
        annotated_events.append(annotated_event)
    return annotated_events


def _checkpoint_transition(
    previous_state: dict[str, Any] | None,
    current_state: dict[str, Any],
    *,
    sequence: int,
) -> dict[str, Any]:
    """返回给 UI 使用的 Checkpoint 迁移名称、差异摘要和持久化原因。"""

    previous = previous_state or {}
    previous_status = str(previous.get("status") or "未创建")
    current_status = str(current_state.get("status") or "unknown")
    changed_fields = {
        key
        for key in current_state
        if key not in {"created_at", "updated_at"}
        and previous.get(key) != current_state.get(key)
    }

    if previous_state is None:
        label = "创建可恢复任务"
        reason = "首次模型调用前建立 run 身份和初始恢复点"
    elif current_status == "waiting_approval":
        label = "进入审批等待"
        reason = "持久状态变更操作启动前形成可恢复人工屏障，进程退出也不会丢失请求"
    elif previous_status == "waiting_approval" and current_status == "running":
        label = "审批后恢复运行"
        reason = "记录批准已被消费，恢复时不再重复发起同一审批"
    elif current_status == "waiting_human":
        label = "等待人工输入"
        reason = "保存 request_id 与恢复位置，回答后可从同一问题继续"
    elif previous_status == "waiting_human" and current_status == "running":
        label = "人工回答后恢复"
        reason = "记录人工回答已进入会话，避免恢复后重复提问"
    elif current_status in {"completed", "blocked", "failed", "cancelled", "paused"}:
        label = f"保存{_display_value(current_status)}状态"
        reason = "统一落盘最终状态、停止原因和恢复建议，避免报告与任务状态分叉"
    elif "last_observation" in changed_fields or _count_increased(
        previous,
        current_state,
        "observations_count",
    ):
        label = "保存工具结果"
        reason = "标记工具结果和 Observation 已提交，恢复时不重复执行已完成操作"
    elif "context_digest" in changed_fields:
        label = "保存上下文摘要"
        reason = "保留压缩边界，使 continuation 能重建有界上下文"
    elif "current_step" in changed_fields:
        label = f"记录第 {int(current_state.get('current_step') or 0)} 轮起点"
        reason = "模型调用前保存当前轮次和会话计数，异常中断后可定位进度"
    elif "metadata" in changed_fields:
        label = "保存恢复定位信息"
        reason = "持久化人工请求、执行环境等 continuation 所需元数据"
    elif changed_fields & {"messages_count", "observations_count"}:
        label = "刷新会话进度"
        reason = "使 durable state 与已经落盘的消息和 Observation 数量一致"
    else:
        label = "刷新任务快照"
        reason = "保存最新可恢复状态；业务字段未发生新的语义转换"

    return {
        "sequence": sequence,
        "label": label,
        "reason": reason,
        "status": current_status,
        "changed_fields": sorted(changed_fields),
        "change_summary": _checkpoint_change_summary(previous, current_state),
    }


def _count_increased(
    previous_state: dict[str, Any],
    current_state: dict[str, Any],
    field_name: str,
) -> bool:
    """动态 JSON 计数字段缺失时按零处理。"""

    return int(current_state.get(field_name) or 0) > int(
        previous_state.get(field_name) or 0
    )


def _checkpoint_change_summary(
    previous_state: dict[str, Any],
    current_state: dict[str, Any],
) -> str:
    """生成一行可扫读的状态差异，不倾倒完整 Checkpoint JSON。"""

    previous_status = str(previous_state.get("status") or "未创建")
    current_status = str(current_state.get("status") or "unknown")
    parts = [
        f"{_display_value(previous_status)} → {_display_value(current_status)}",
        f"step={int(current_state.get('current_step') or 0)}",
    ]
    last_tool = str(current_state.get("last_tool") or "")
    if last_tool:
        parts.append(f"tool={last_tool}")
    for field_name, label in (
        ("messages_count", "消息"),
        ("observations_count", "观察"),
    ):
        before = int(previous_state.get(field_name) or 0)
        after = int(current_state.get(field_name) or 0)
        if before != after:
            parts.append(f"{label} {before}→{after}")
    return " · ".join(parts)


def _render_timeline_turn(turn: int, events: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {
        stage[0]: [] for stage in _TIMELINE_STAGES
    }
    for event in events:
        event_type = str(event.get("event_type") or "")
        grouped[_TRACE_STAGE_BY_EVENT.get(event_type, "persistence")].append(event)
    stages = "".join(
        _render_timeline_stage(key, number, title, note, tone, grouped[key])
        for key, number, title, note, tone in _TIMELINE_STAGES
    )
    stage_evidence = "".join(
        _render_raw_event_details(
            grouped[key],
            summary=f"{number} {title}：查看本段底层证据",
        )
        for key, number, title, _note, _tone in _TIMELINE_STAGES
        if grouped[key]
    )
    outcome, outcome_tone = _timeline_turn_outcome(events)
    turn_summary = _timeline_turn_summary(events)
    return (
        "<div class='timeline-turn'>"
        "<div class='timeline-head'>"
        f"<div><strong>第 {turn} 轮 · {_escape(turn_summary)}</strong>"
        "<small>一次模型决策及其后续动作</small></div>"
        f"{_badge(outcome, outcome_tone)}</div>"
        f"<div class='timeline-phase-grid'>{stages}</div>"
        f"<div class='timeline-stage-drilldowns'>{stage_evidence}</div>"
        "</div>"
    )


def _render_timeline_stage(
    key: str,
    number: str,
    title: str,
    note: str,
    tone: str,
    events: list[dict[str, Any]],
) -> str:
    if events:
        summary = _summarize_timeline_stage(key, events)
    else:
        summary = "本轮无需执行" if key == "governed_execution" else "未观测"
    state = " empty" if not events else ""
    return (
        f"<div class='timeline-phase {tone}{state}'>"
        f"<div class='timeline-phase-head'><b>{number}</b><span>{_escape(title)}</span></div>"
        f"<strong>{_escape(summary)}</strong>"
        f"<small>{_escape(note)}</small>"
        "</div>"
    )


def _summarize_timeline_stage(key: str, events: list[dict[str, Any]]) -> str:
    if key == "input":
        assembly = _last_trace_event(events, "context_assembly")
        window_event = _last_trace_event(events, "context_window")
        context = (assembly or {}).get("context") or {}
        window = (window_event or {}).get("context_window") or {}
        parts = []
        estimated_tokens = window.get("estimated_tokens_after")
        if estimated_tokens is not None:
            parts.append(f"约 {int(estimated_tokens):,} tokens")
        tools = context.get("available_tools") or []
        if tools:
            parts.append(f"{len(tools)} 个工具")
        if bool(window.get("compacted")):
            parts.append("已压缩")
        elif window_event is not None:
            parts.append("无需压缩")
        return " · ".join(parts) or "Turn 输入已准备"

    if key == "decision":
        completed = _last_trace_event(events, "llm_call")
        started = _last_trace_event(events, "model_started")
        usage = (completed or {}).get("model_usage") or {}
        request = (started or {}).get("model_request") or {}
        metrics = []
        if usage.get("latency_ms") is not None:
            metrics.append(_format_milliseconds(int(usage["latency_ms"])))
        if usage.get("total_tokens") is not None:
            metrics.append(f"{int(usage['total_tokens']):,} tokens")
        rejected_tool_request = _pending_tool_rejection(events)
        final_answer = _last_trace_event(events, "final_answer")
        if rejected_tool_request is not None:
            requested_tool = _pending_tool_name(events, rejected_tool_request)
            decision = f"收口失败：仍请求 {requested_tool}，该工具没有执行"
        elif final_answer is not None:
            decision = _compact_timeline_text(
                final_answer.get("observation") or "模型给出最终回答"
            )
        else:
            latest_action_event = _last_trace_event(events, "action")
            if latest_action_event is not None:
                tool_name = str(latest_action_event.get("tool_call") or "未知工具")
                tool_target = _trace_tool_target(latest_action_event)
                decision = f"请求 {tool_name}"
                if tool_target:
                    decision += f" · {tool_target}"
            elif request.get("messages_count") is not None:
                decision = f"处理 {int(request['messages_count'])} 条消息"
            else:
                decision = "模型边界已记录"
        return " · ".join([decision, *metrics])

    if key == "governed_execution":
        checks = [
            event
            for event in events
            if str(event.get("event_type") or "")
            in {
                "guardrail_check",
                "hook_check",
                "permission_check",
                "human_approval",
                "human_input_requested",
                "run_control",
                "operation_ledger",
            }
        ]
        governance_blocked = any(
            str(event.get("permission_decision") or "").lower() == "deny"
            or (
                str(event.get("event_type") or "")
                in {
                    "guardrail_check",
                    "hook_check",
                    "permission_check",
                    "human_approval",
                    "human_input_requested",
                    "run_control",
                }
                and not bool(event.get("success", True))
            )
            for event in events
        )
        waiting_for_operator = any(
            str(event.get("event_type") or "") == "human_input_requested"
            or (
                str(event.get("event_type") or "") == "human_approval"
                and str(event.get("observation") or "") in {"pending", "requested"}
            )
            or str(event.get("permission_decision") or "").lower() == "ask"
            for event in events
        )
        if governance_blocked:
            return f"治理阻断 · {len(checks)} 项检查"
        if waiting_for_operator:
            return f"等待人工决策 · {len(checks)} 项检查"
        observations = [
            event
            for event in events
            if str(event.get("event_type") or "") == "tool_observation"
        ]
        if observations:
            tools = _ordered_tool_names(observations)
            failures = sum(
                1 for event in observations if not bool(event.get("success", True))
            )
            suffix = f"{failures} 次失败" if failures else "成功"
            check_text = f"{len(checks)} 项治理检查 · " if checks else ""
            return f"{check_text}{', '.join(tools)} · {suffix}"
        started = _last_trace_event(events, "tool_execution_started")
        if started is not None:
            return f"{len(checks)} 项治理检查 · {started.get('tool_call') or '工具'} 已开始"
        if checks:
            return f"{len(checks)} 项治理检查通过 · 本轮未调用工具"
        return "本轮未调用工具"

    completed = _last_trace_event(events, "run_completed")
    if completed is not None:
        status = _display_value(completed.get("run_status") or "completed")
        reason = str(completed.get("stop_reason") or "")
        return f"{status}" + (f" · {_display_value(reason)}" if reason else "")
    checkpoint = _last_trace_event(events, "task_state_checkpoint")
    if checkpoint is not None:
        state = checkpoint.get("task_state") or {}
        status = _display_value(state.get("status") or "saved")
        checkpoint_events = [
            event
            for event in events
            if str(event.get("event_type") or "") == "task_state_checkpoint"
        ]
        latest_transition = checkpoint.get("_checkpoint_transition") or {}
        transition_label = str(latest_transition.get("label") or "保存任务状态")
        message_count_value = state.get("messages_count")
        observation_count_value = state.get("observations_count")
        counts = ""
        if message_count_value is not None or observation_count_value is not None:
            counts = (
                f"{int(message_count_value or 0)} 条消息 · "
                f"{int(observation_count_value or 0)} 条观察"
            )
        return (
            f"{len(checkpoint_events)} 次状态写入 · {transition_label} · {status}"
            + (f" · {counts}" if counts else "")
        )
    return f"{len(events)} 个状态 / 证据事件"


def _render_raw_event_details(
    events: list[dict[str, Any]],
    *,
    summary: str,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{index}</td>"
        f"<td>{_escape(_event_business_label(event))}</td>"
        f"<td><code>{_escape(str(event.get('event_type') or ''))}</code></td>"
        f"<td>{_escape(_event_subject(event))}</td>"
        f"<td>{_escape(_event_learning_reason(event))}</td>"
        f"<td>{_render_event_result(event)}</td>"
        f"<td>{_escape(_format_milliseconds(int(event.get('duration_ms') or 0)))}</td>"
        "</tr>"
        for index, event in enumerate(events, start=1)
    )
    return (
        "<details class='timeline-raw drilldown'>"
        f"<summary>{_escape(summary)}（{len(events)}）</summary>"
        "<div class='timeline-raw-events'><table><thead><tr>"
        "<th>#</th><th>业务含义</th><th>机器事件</th><th>状态变化 / 对象</th>"
        "<th>为什么记录</th><th>状态 / 结果</th><th>距上一事件</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
        "</details>"
    )


def _timeline_turn_summary(events: list[dict[str, Any]]) -> str:
    """用本轮主动作替代没有业务含义的 event 数量。"""

    rejected_tool_request = _pending_tool_rejection(events)
    if rejected_tool_request is not None:
        requested_tool = _pending_tool_name(events, rejected_tool_request)
        return f"收口失败：仍请求 {requested_tool}，未执行"
    final_answer = _last_trace_event(events, "final_answer")
    if final_answer is not None:
        return "形成最终回答"
    action = _last_trace_event(events, "action")
    if action is not None:
        tool = str(action.get("tool_call") or "工具")
        target = _trace_tool_target(action)
        return f"模型请求 {tool}" + (f" · {target}" if target else "")
    if _last_trace_event(events, "llm_call") is not None:
        return "模型完成一次判断"
    return "运行状态推进"


def _pending_tool_rejection(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """读取新格式拒绝事件，并兼容旧 Trace 中误名为 final_answer 的事件。"""

    explicit_event = _last_trace_event(events, "pending_tool_call_rejected")
    if explicit_event is not None:
        return explicit_event
    legacy_final_event = _last_trace_event(events, "final_answer")
    if legacy_final_event is not None and legacy_final_event.get("pending_tool_call"):
        return legacy_final_event
    return None


def _pending_tool_name(
    events: list[dict[str, Any]],
    rejection_event: dict[str, Any],
) -> str:
    """优先读结构化工具名；旧 Trace 再从模型响应摘要中恢复名称。"""

    tool_name = str(rejection_event.get("tool_call") or "").strip()
    if tool_name:
        return tool_name
    llm_call = _last_trace_event(events, "llm_call") or {}
    response_summary = str(llm_call.get("llm_response_summary") or "")
    match = re.search(
        r'invoke\s+name=["\']([^"\']+)["\']',
        response_summary,
        re.IGNORECASE,
    )
    return match.group(1) if match else "未识别工具"


def _annotate_pending_tool_rejections(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """给旧 Trace 的拒绝事件补上工具名，避免详情表只能显示“未识别”。"""

    annotated: list[dict[str, Any]] = []
    for event in events:
        event_copy = dict(event)
        is_legacy_rejection = event_copy.get(
            "event_type"
        ) == "final_answer" and event_copy.get("pending_tool_call")
        if is_legacy_rejection and not event_copy.get("tool_call"):
            event_copy["tool_call"] = _pending_tool_name(
                [*annotated, event_copy],
                event_copy,
            )
        annotated.append(event_copy)
    return annotated


def _hook_stage(event: dict[str, Any]) -> str:
    """返回 Hook 生命周期阶段，并兼容早期未写 ``before_tool`` 的 Trace。"""

    explicit_stage = str(event.get("hook_stage") or "")
    if explicit_stage:
        return explicit_stage
    if event.get("tool_call"):
        return "before_tool"
    return "unknown"


def _hook_result(event: dict[str, Any]) -> dict[str, Any]:
    """读取聚合后的 Hook 决定；Hook 异常事件可能没有该结构。"""

    result = event.get("hook_result")
    return result if isinstance(result, dict) else {}


def _hook_subject(event: dict[str, Any]) -> str:
    """把 Hook 阶段、治理对象和最终决定压缩成一行可读事实。"""

    stage = _hook_stage(event)
    result = _hook_result(event)
    decision = _display_value(result.get("decision") or "unknown")
    decisions = [
        item for item in result.get("decisions") or [] if isinstance(item, dict)
    ]
    decisive_hooks = [
        f"{item.get('hook_name')}: {_display_value(item.get('decision') or 'unknown')}"
        for item in decisions
        if str(item.get("decision") or "").lower() != "defer"
    ]
    if decisive_hooks:
        policy_summary = "、".join(decisive_hooks)
    elif decisions:
        policy_summary = f"{len(decisions)} 个处理器均无额外意见"
    else:
        policy_summary = str(event.get("hook_name") or "未记录具体处理器")

    governed_object = str(event.get("tool_call") or "模型调用")
    if stage == "on_checkpoint":
        governed_object = "Checkpoint"
    elif stage == "on_stop":
        governed_object = "运行完成声明"
    return f"{governed_object} · 最终决定={decision} · {policy_summary}"


def _hook_learning_reason(event: dict[str, Any]) -> str:
    """说明生命周期处理器在当前时机做什么，不把 Hook 统称为校验。"""

    stage = _hook_stage(event)
    tool = str(event.get("tool_call") or "工具")
    stage_reason = {
        "before_model": (
            "在上下文发送给模型前汇总外部策略；拒绝或人工介入会阻止本轮模型调用。"
        ),
        "after_model": "模型返回后执行规范化和脱敏，避免未经处理的响应直接进入动作解析。",
        "before_tool": (
            f"在 {tool} 执行前汇总环境与权限策略；决定允许、转人工或拒绝，"
            "防止工具绕过治理。"
        ),
        "after_tool": "工具返回后处理和脱敏 Observation，再把结果交回模型。",
        "on_checkpoint": "Checkpoint 落盘后通知审计或指标扩展，不改变已持久化状态。",
        "on_stop": "Runtime 宣称完成前执行外部质量门，阻止缺证据或未收口的结果。",
    }.get(stage, "记录生命周期处理器介入的时机和结果，避免控制流不可见。")

    result = _hook_result(event)
    decision = result.get("decision")
    reason = result.get("reason")
    if decision or reason:
        conclusion = _display_value(decision or "unknown")
        detail = _translate_runtime_summary(reason or "未记录理由")
        return f"{stage_reason} 本次结论：{conclusion}（{detail}）。"
    if not bool(event.get("success", True)):
        policy = _display_value(event.get("failure_policy") or "unknown")
        return f"{stage_reason} 本次处理器异常，失败策略={policy}。"
    return stage_reason


def _event_subject(event: dict[str, Any]) -> str:
    """提取原始事件中最值得排障者看的对象，不直接倾倒完整 JSON。"""

    checkpoint_transition = event.get("_checkpoint_transition")
    if isinstance(checkpoint_transition, dict):
        return str(checkpoint_transition.get("change_summary") or "任务快照已保存")
    event_type = str(event.get("event_type") or "")
    if event_type == "pending_tool_call_rejected" or (
        event_type == "final_answer" and event.get("pending_tool_call")
    ):
        tool = str(event.get("tool_call") or "未识别工具")
        return f"{tool} · 未进入工具执行链"
    if event_type == "hook_check":
        return _hook_subject(event)
    if event_type == "permission_check":
        tool = str(event.get("tool_call") or "工具")
        decision = _display_value(event.get("permission_decision") or "unknown")
        reason = _translate_runtime_summary(event.get("reason") or "未记录理由")
        return f"{tool} · 最终权限={decision} · {reason}"
    if event_type == "guardrail_check":
        guardrail = event.get("guardrail") or {}
        if isinstance(guardrail, dict):
            category = _display_value(guardrail.get("category") or "unknown")
            outcome = (
                "检查通过" if bool(guardrail.get("passed", True)) else "记录到问题"
            )
            return f"{category} · {outcome}"
    if event_type == "validation_evidence":
        validation = event.get("validation") or {}
        if isinstance(validation, dict):
            kind = str(validation.get("kind") or "验证")
            status = _display_value(validation.get("status") or "unknown")
            tool = str(validation.get("tool") or "")
            return " · ".join(part for part in (kind, tool, status) if part)
    if event_type == "operation_ledger":
        tool = str(event.get("tool_call") or "状态变更操作")
        status = _display_value(event.get("operation_status") or "unknown")
        return f"{tool} · 操作状态={status}"
    if event_type == "run_completed":
        status = _display_value(event.get("run_status") or "unknown")
        reason = _display_value(event.get("stop_reason") or "unknown")
        return f"最终状态={status} · 停止原因={reason}"
    fallback_tool_name = str(event.get("tool_call") or "")
    if fallback_tool_name:
        target = _trace_tool_target(event)
        return f"{fallback_tool_name}" + (f" · {target}" if target else "")
    for key in (
        "permission_decision",
        "decision",
        "run_status",
        "stop_reason",
        "agent_name",
    ):
        if event.get(key) not in (None, ""):
            return f"{key}={_display_value(event[key])}"
    return "-"


def _event_business_label(event: dict[str, Any]) -> str:
    """Checkpoint 和 Hook 使用阶段名称；其他事件使用稳定业务标签。"""

    checkpoint_transition = event.get("_checkpoint_transition")
    if isinstance(checkpoint_transition, dict):
        return str(checkpoint_transition.get("label") or "保存任务状态")
    event_type = str(event.get("event_type") or "未知事件")
    if event_type == "hook_check":
        return _HOOK_STAGE_LABELS.get(_hook_stage(event), "生命周期处理器事件")
    if event_type == "final_answer" and event.get("pending_tool_call"):
        return "收口失败：工具请求未执行"
    return _TRACE_EVENT_LABELS.get(event_type, event_type)


def _event_learning_reason(event: dict[str, Any]) -> str:
    """说明事件保护的运行边界，而不是统一写成“用于排障”。"""

    checkpoint_transition = event.get("_checkpoint_transition")
    if isinstance(checkpoint_transition, dict):
        return str(
            checkpoint_transition.get("reason")
            or "保存最新可恢复状态，供中断续跑和审计使用"
        )
    event_type = str(event.get("event_type") or "")
    if event_type == "pending_tool_call_rejected" or (
        event_type == "final_answer" and event.get("pending_tool_call")
    ):
        tool = str(event.get("tool_call") or "未识别工具")
        return (
            f"最终轮已经关闭工具执行，但模型仍请求 {tool}；Runtime 拒绝该调用并阻断运行，"
            "防止把尚未执行的动作误报为完成。"
        )
    if event_type == "hook_check":
        return _hook_learning_reason(event)
    if event_type == "permission_check":
        tool = str(event.get("tool_call") or "工具")
        decision = _display_value(event.get("permission_decision") or "unknown")
        reason = _translate_runtime_summary(event.get("reason") or "未记录理由")
        return (
            f"把工具执行前处理器的结果收敛为 Runtime 对 {tool} 的执行许可："
            f"{decision}（{reason}）；ASK 才进入人工授权，恢复状态另由操作状态表负责。"
        )
    if event_type == "tool_execution_started":
        tool = str(event.get("tool_call") or "工具")
        return (
            f"标记 {tool} 已跨过治理边界并开始执行，用来区分执行前阻断与工具内部失败。"
        )
    if event_type == "tool_call":
        tool = str(event.get("tool_call") or "工具")
        return (
            f"记录 {tool} 的调用标识和参数摘要，用于统计、关联 Observation 与失败归因。"
        )
    if event_type == "tool_observation":
        tool = str(event.get("tool_call") or "工具")
        return (
            f"记录 {tool} 返回给 Agent 的结果；下一轮模型判断将基于这条 Observation。"
        )
    if event_type == "validation_evidence":
        validation = event.get("validation") or {}
        kind = (
            str(validation.get("kind") or "验证")
            if isinstance(validation, dict)
            else "验证"
        )
        return f"保存 {kind} 的命令、退出码和摘要，让“验证通过”能够被独立复核。"
    if event_type == "operation_ledger":
        return "记录持久状态变更操作处于计划、执行中或已完成，供恢复时判断继续、回填旧结果或阻断。"

    purpose = _TRACE_EVENT_PURPOSES.get(event_type)
    if purpose:
        return purpose
    return f"未注册的事件 {event_type or 'unknown'}；保留原始事实，避免展示层静默丢失证据。"


def _render_event_result(event: dict[str, Any]) -> str:
    """优先显示业务决定；只有无业务状态时才显示记录是否成功。"""

    checkpoint_transition = event.get("_checkpoint_transition")
    if isinstance(checkpoint_transition, dict):
        status = str(checkpoint_transition.get("status") or "saved")
        return _badge(status, _tone_for_status(status))
    event_type = str(event.get("event_type") or "")
    if event_type == "pending_tool_call_rejected" or (
        event_type == "final_answer" and event.get("pending_tool_call")
    ):
        return _badge("blocked", "bad")
    if not bool(event.get("success", True)):
        return _badge("failed", "bad")
    if event_type == "hook_check":
        decision = str(_hook_result(event).get("decision") or "success")
        return _badge(decision, _tone_for_status(decision))
    if event_type == "permission_check":
        decision = str(event.get("permission_decision") or "success")
        return _badge(decision, _tone_for_status(decision))
    if event_type == "validation_evidence":
        validation = event.get("validation") or {}
        if isinstance(validation, dict):
            status = str(validation.get("status") or "success")
            return _badge(status, _tone_for_status(status))
    if event_type == "operation_ledger":
        status = str(event.get("operation_status") or "success")
        return _badge(status, _tone_for_status(status))
    succeeded = bool(event.get("success", True))
    return _badge("success" if succeeded else "failed", "ok" if succeeded else "bad")


def _timeline_turn_outcome(events: list[dict[str, Any]]) -> tuple[str, str]:
    completed = _last_trace_event(events, "run_completed")
    if completed is not None:
        status = str(completed.get("run_status") or "completed")
        return _display_value(status), _tone_for_status(status)
    if any(not bool(event.get("success", True)) for event in events):
        return "存在失败", "bad"
    if any(
        str(event.get("event_type") or "")
        in {"human_approval", "human_input_requested"}
        for event in events
    ):
        return "等待人工", "warn"
    if _last_trace_event(events, "tool_observation") is not None:
        return "工具完成", "ok"
    if _last_trace_event(events, "llm_call") is not None:
        return "模型完成", "neutral"
    return "正常推进", "neutral"


def _last_trace_event(
    events: list[dict[str, Any]],
    event_type: str,
) -> dict[str, Any] | None:
    for trace_event in reversed(events):
        current_event_type = str(trace_event.get("event_type") or "")
        if current_event_type == event_type:
            return trace_event
    return None


def _ordered_tool_names(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        name = str(event.get("tool_call") or "工具")
        if name not in names:
            names.append(name)
    return names


def _trace_tool_target(event: dict[str, Any]) -> str:
    arguments = event.get("tool_arguments") or {}
    if not isinstance(arguments, dict):
        return ""
    for key in ("path", "target", "keyword", "query", "kind"):
        value = arguments.get(key)
        if value:
            return _compact_timeline_text(value, max_chars=72)
    return ""


def _compact_timeline_text(value: object, *, max_chars: int = 96) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _format_milliseconds(value: int) -> str:
    return f"{value / 1000:.2f}s" if value >= 1000 else f"{value}ms"


def _render_run_evidence(project_dir: Path) -> str:
    run_dir = _latest_run_dir(project_dir)
    fanout_path = _latest_fanout_summary_path(project_dir)
    fanout = _read_json_file(fanout_path)
    if fanout:
        return _render_fanout_run_evidence(fanout, fanout_path)

    run_story = None
    run_story_error = ""
    try:
        run_story = _latest_run_story(project_dir)
    except (OSError, ValueError) as exc:
        run_story_error = str(exc)
    comparison_path = _latest_comparison_path(project_dir)
    multi_path = _latest_multi_agent_summary_path(project_dir)
    usage_path = _latest_usage_path(project_dir)
    trace_path = _latest_trace_path(project_dir)

    comparison = _read_json_file(comparison_path)
    multi = _read_json_file(multi_path)
    usage = _read_json_file(usage_path)
    trace = _read_json_file(trace_path)
    summary = usage.get("summary") or {}

    single_status = (
        run_story.status
        if run_story is not None
        else comparison.get("single_status") or "-"
    )
    failure = comparison.get("failure_taxonomy") or "unclassified"
    cost = float(summary.get("estimated_cost_usd") or 0.0)
    task_id = (
        run_story.task
        if run_story is not None and run_story.task
        else comparison.get("task_id")
        or multi.get("task")
        or trace.get("task")
        or "最近一次本地运行"
    )
    active_skills = summary.get("active_skills") or []
    heading_status = run_story.status if run_story is not None else single_status
    optional_multi_sections = ""
    if multi.get("role_results") or multi.get("artifacts"):
        optional_multi_sections = (
            "<details class='drilldown'><summary>查看本次运行的角色决策与交接产物</summary>"
            "<div class='drilldown-body'><h4>角色决策</h4>"
            "<table><thead><tr><th>角色</th><th>决策</th><th>轮次</th><th>证据摘要</th></tr></thead>"
            f"<tbody>{_render_role_decision_rows(multi)}</tbody></table>"
            f"{_render_artifact_cards(multi)}</div></details>"
        )
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>单次运行</span><h2>运行证据总览</h2></div>"
        f"{_badge(heading_status, _tone_for_status(heading_status))}</div>",
        _metric_grid(
            [
                (
                    "运行状态",
                    _display_value(single_status),
                    "当前 Single-Run",
                    _tone_for_status(str(single_status)),
                ),
                (
                    "模型调用",
                    str(summary.get("llm_calls", 0)),
                    "本次实际调用次数",
                    "neutral",
                ),
                (
                    "工具调用",
                    str(summary.get("tool_calls", 0)),
                    f"失败 {int(summary.get('failed_tool_calls') or 0)} 次",
                    "bad" if summary.get("failed_tool_calls") else "ok",
                ),
                (
                    "Checkpoint",
                    str(
                        summary.get(
                            "checkpoints", summary.get("task_state_checkpoints", 0)
                        )
                    ),
                    "持久化状态记录",
                    "neutral",
                ),
                ("估算成本", f"${cost:.6f}", "当前运行记录", "neutral"),
                (
                    "失败分类",
                    _display_value(failure),
                    "只有明确诊断时才成立",
                    _tone_for_status(str(failure)),
                ),
            ]
        ),
        f"<p class='task-summary'><span>本次目标</span>{_escape(str(task_id)[:420])}</p>",
        _render_run_story_section(run_story, run_dir, run_story_error),
        "<details class='drilldown'><summary>查看本次触发的上下文、记忆、Skill 与工具适配信号</summary>"
        "<div class='drilldown-body'><div class='capability-strip'>"
        f"<div><b>{int(summary.get('compacted_context_turns') or 0)}</b><span>上下文压缩轮次</span></div>"
        f"<div><b>{int(summary.get('memory_recalled') or 0)}</b><span>召回记忆数量</span></div>"
        f"<div><b>{int(summary.get('tool_call_repairs') or 0)}</b><span>工具调用修复</span></div>"
        f"<div><b>{int(summary.get('bounded_tool_call_bursts') or 0)}</b><span>工具突发拦截</span></div>"
        "</div>"
        f"<p><span class='label'>已激活 Skill</span>{_escape(', '.join(str(item) for item in active_skills) or '本次未观测')}</p>"
        "<p class='boundary-note'>数值为 0 表示本次没有触发，不代表该能力已经验证通过。</p></div></details>",
        optional_multi_sections,
        "<details class='provenance'><summary>产物来源</summary>"
        f"<code>{_escape(str(run_dir or '未找到'))}</code><code>{_escape(str(comparison_path or '未找到'))}</code>"
        f"<code>{_escape(str(trace_path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_fanout_run_evidence(
    fanout: dict[str, Any],
    summary_path: Path | None,
) -> str:
    """把 Fanout 的分散 Worker/合并/Finalizer 产物投影成一条可读主链。"""

    metrics = fanout.get("metrics") or {}
    results = [
        result for result in fanout.get("results") or [] if isinstance(result, dict)
    ]
    batches = fanout.get("batches") or []
    conflicts = fanout.get("conflicts") or []
    merged_task_ids = fanout.get("merged_task_ids") or []
    fanout_dir = summary_path.parent if summary_path is not None else None
    plan_path = fanout_dir / "fanout_plan.json" if fanout_dir else None
    checkpoint_path = fanout_dir / "fanout_checkpoint.json" if fanout_dir else None
    diff_path = fanout_dir / "integrated_changes.diff" if fanout_dir else None
    report_path = fanout_dir / "fanout_report.md" if fanout_dir else None
    finalizer_trace_value = str(fanout.get("finalizer_trace_path") or "")
    finalizer_usage_value = str(fanout.get("finalizer_usage_path") or "")
    finalizer_trace_path = (
        Path(finalizer_trace_value) if finalizer_trace_value else None
    )
    finalizer_usage_path = (
        Path(finalizer_usage_value) if finalizer_usage_value else None
    )
    finalizer_verification_path = (
        finalizer_trace_path.parent / "verification.md"
        if finalizer_trace_path is not None
        else None
    )
    worker_tools = sum(
        int((result.get("usage_summary") or {}).get("tool_calls") or 0)
        for result in results
    )
    finalizer_tools = int(
        (fanout.get("finalizer_usage_summary") or {}).get("tool_calls") or 0
    )
    model_calls = int(metrics.get("llm_calls") or 0)
    tool_calls = int(metrics.get("tool_calls") or worker_tools + finalizer_tools)
    failed_tool_calls = sum(
        int((result.get("usage_summary") or {}).get("failed_tool_calls") or 0)
        for result in results
    ) + int((fanout.get("finalizer_usage_summary") or {}).get("failed_tool_calls") or 0)
    final_decision = str(fanout.get("final_decision") or "not_run")
    status = str(fanout.get("status") or "unknown")
    checkpoint_count = int(checkpoint_path is not None and checkpoint_path.is_file())

    touched_files = sorted(
        {str(path) for result in results for path in result.get("touched_files") or []}
    )
    worker_evidence = (
        ", ".join(
            f"{result.get('task_id', 'worker')}={_display_value(result.get('status', 'unknown'))}"
            for result in results
        )
        or "没有 Worker 结果"
    )
    stages = [
        (
            "计划与依赖检查",
            bool(batches),
            f"{len(results)} 个任务被编排为 {len(batches)} 个依赖批次",
            "LiveFanoutCoordinator.run",
            plan_path,
        ),
        (
            "Worker 隔离执行",
            bool(results)
            and all(result.get("status") == "completed" for result in results),
            worker_evidence,
            "LiveFanoutCoordinator._run_batch",
            None,
        ),
        (
            "改动范围与冲突门禁",
            not conflicts and bool(results),
            (
                f"改动文件：{', '.join(touched_files) or '无'}；"
                f"检测到 {len(conflicts)} 个范围冲突"
            ),
            "LiveFanoutCoordinator._mark_dynamic_conflicts",
            None,
        ),
        (
            "候选改动合并",
            bool(merged_task_ids) and _path_is_file(diff_path),
            f"已合并：{', '.join(str(item) for item in merged_task_ids) or '无'}",
            "LiveFanoutCoordinator._merge_batch",
            diff_path,
        ),
        (
            "隔离 Finalizer",
            final_decision.upper() == "PASS",
            f"最终决策：{_display_value(final_decision)}；执行只读聚焦验证",
            "LocalAgentWorkerAdapter.run_finalizer",
            finalizer_verification_path,
        ),
        (
            "Checkpoint 与证据发布",
            bool(summary_path and summary_path.is_file()),
            "协调状态、Worker 结果、合并改动与验证结论已经持久化",
            "LiveFanoutCoordinator.run",
            summary_path,
        ),
    ]
    stage_cards = []
    for index, (title, observed, detail, owner, artifact_path) in enumerate(
        stages,
        start=1,
    ):
        state = "已观测" if observed else "未观测"
        artifact_text = (
            str(artifact_path)
            if artifact_path is not None and artifact_path.is_file()
            else "本阶段没有独立文件，结论来自 Fanout 汇总"
        )
        stage_cards.append(
            "<article class='story-stage'>"
            "<div class='story-stage-head'>"
            f"<b>{index:02d}</b><div><h4>{_escape(title)}</h4>"
            f"<p>{_escape(detail)}</p></div>"
            f"{_badge(state, 'ok' if observed else 'neutral')}</div>"
            "<details><summary>实现入口与证据文件</summary>"
            f"<div class='story-stage-detail'><code>{_escape(owner)}</code>"
            f"<span>{_escape(artifact_text)}</span></div></details>"
            "</article>"
        )

    candidate_state = "present" if _path_is_file(diff_path) else "absent"
    local_state = "passed" if final_decision.upper() == "PASS" else "not_run"
    ladder = "".join(
        [
            _claim_step(
                "候选结果",
                candidate_state,
                "合并后的候选 Diff 已生成，不代表官方正确",
                _tone_for_evidence_state(candidate_state),
            ),
            _claim_step(
                "本地验证",
                local_state,
                "Finalizer 的聚焦 pytest 结论",
                _tone_for_status(local_state),
            ),
            _claim_step(
                "官方评测",
                "not_evaluated",
                "该协同运行验证编排机制，不执行官方 Benchmark",
                "neutral",
            ),
        ]
    )
    provenance_paths = [
        path
        for path in (
            plan_path,
            checkpoint_path,
            diff_path,
            report_path,
            finalizer_trace_path,
            finalizer_usage_path,
            summary_path,
        )
        if path is not None and str(path)
    ]
    provenance = "".join(
        f"<code>{_escape(str(path))}</code>" for path in provenance_paths
    )
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>多 AGENT 单次运行</span>"
        "<h2>多 Agent 运行证据</h2></div>"
        f"{_badge(status, _tone_for_status(status))}</div>",
        "<p class='help strong'>本页聚合 Coordinator、多个隔离 Worker、候选改动合并和 Finalizer 的分散证据。"
        "这里的“模型调用”经过真实 ModelPort，但使用确定性本地适配器，不会产生外部 API Token 或费用。</p>",
        _metric_grid(
            [
                (
                    "运行状态",
                    _display_value(status),
                    "Fanout 协调器最终状态",
                    _tone_for_status(status),
                ),
                (
                    "模型调用（确定性）",
                    str(model_calls),
                    f"{len(results)} 个 Worker + Finalizer",
                    "neutral",
                ),
                (
                    "工具调用",
                    str(tool_calls),
                    f"Worker {worker_tools} 次 + Finalizer {finalizer_tools} 次；失败 {failed_tool_calls} 次",
                    "ok",
                ),
                (
                    "协调器 Checkpoint",
                    str(checkpoint_count),
                    "保存协调器当前可恢复状态，不是每个 Turn 一个",
                    "ok" if checkpoint_count else "neutral",
                ),
                (
                    "Token / 成本",
                    f"{int(metrics.get('total_tokens') or 0)} / $0",
                    "未调用外部大模型",
                    "neutral",
                ),
                (
                    "最终验证",
                    _display_value(final_decision),
                    "聚焦 pytest；不是官方评测",
                    _tone_for_status(final_decision),
                ),
            ]
        ),
        f"<p class='task-summary'><span>本次目标</span>{_escape(fanout.get('goal') or '')}</p>",
        _render_fanout_scenario_contract(summary_path),
        _render_fanout_task_contract(fanout, summary_path),
        "<section class='evidence-section run-story'>"
        "<div class='section-title'><h3>运行全链路</h3>"
        "<span>计划 → Worker → 门禁 → 合并 → 验证 → 发布</span></div>"
        "<div class='section-title run-story-subtitle'><h4>当前证据最多支持什么结论</h4>"
        "<span>本地通过不等于官方解决</span></div>"
        f"<div class='claim-ladder run-story-ladder'>{ladder}</div>"
        "<div class='section-title run-story-subtitle'><h4>主链阶段</h4>"
        "<span>实现类和文件路径默认折叠</span></div>"
        f"<div class='story-stage-list'>{''.join(stage_cards)}</div>"
        "<details class='provenance'><summary>查看全部证据文件</summary>"
        f"{provenance}</details></section>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _path_is_file(path: Path | None) -> bool:
    return path is not None and path.is_file()


_RUN_STORY_STAGE_EVIDENCE = {
    "request": "任务、配置、工作区和执行环境已经固定",
    "loop": "轮次边界、模型请求和模型响应均有记录",
    "context_model": "模型只接收预算内上下文与允许使用的工具",
    "tool_governance": "工具意图经过路由、权限、幂等与执行结果链",
    "lifecycle": "Checkpoint、人工等待和终止状态可持久化",
    "artifacts": "候选改动、最终回答与运行事实分开保存",
    "evidence": "候选、本地验证和官方评测使用不同结论等级",
}

_ARTIFACT_TITLES = {
    "candidate_diff": "候选代码改动",
    "execution_environment": "执行环境清单",
    "final_answer": "最终回答",
    "run_request": "运行请求",
    "checkpoint": "运行检查点",
    "trace": "运行轨迹",
    "usage_projection": "用量统计",
    "usage_report": "用量报告",
    "local_report": "本地验证报告",
    "official_report": "官方评测报告",
}


def _render_run_story_section(
    story: RunStory | None,
    run_dir: Path | None,
    error: str = "",
) -> str:
    """先展示主链结论，模块名、事件数量和文件血缘按需展开。"""

    manifest_path = run_dir / "run_manifest.json" if run_dir is not None else None
    if story is None:
        message = (
            f"无法读取标准运行清单：{error}。下面只能展示兼容旧格式的证据。"
            if error
            else "最新运行没有标准运行清单，下面只能展示兼容旧格式的证据。"
        )
        return (
            "<section class='evidence-section run-story'>"
            "<div class='section-title'><h3>运行全链路</h3>"
            "<span>兼容旧格式</span></div>"
            f"<div class='diagnosis'>{_escape(message)}</div>"
            "<p class='boundary-note'>此页面只读取已有产物，不会执行工具，也不会提升证据等级。</p>"
            "</section>"
        )

    stage_cards = []
    for index, stage in enumerate(story.stages, start=1):
        state = "observed" if stage.observed else "not_observed"
        artifacts = ", ".join(stage.artifact_ids)
        evidence_summary = _RUN_STORY_STAGE_EVIDENCE.get(
            stage.stage_id,
            "本阶段的运行事实已记录",
        )
        if not stage.observed:
            evidence_summary = "本次运行没有观测到这个阶段"
        artifact_line = (
            f"<span>关联产物：{_escape(artifacts)}</span>"
            if artifacts
            else "<span>本阶段没有独立文件产物</span>"
        )
        stage_cards.append(
            "<article class='story-stage'>"
            "<div class='story-stage-head'>"
            f"<b>{index:02d}</b><div><h4>{_escape(stage.title)}</h4>"
            f"<p>{_escape(evidence_summary)}</p></div>"
            f"{_badge(state, 'ok' if stage.observed else 'neutral')}</div>"
            f"{artifact_line}"
            "<details><summary>实现与底层证据</summary>"
            f"<div class='story-stage-detail'><code>{_escape(stage.owner_symbol)}</code>"
            f"<span>上游入口：{_escape(stage.canonical_upstream)}</span>"
            f"<span>底层证据记录：{stage.event_count} 条。它们是 Trace 记录，不是 {stage.event_count} 个执行步骤。</span>"
            f"<span>必须保持：{_escape(stage.invariant)}</span>"
            f"<span>稳定标识：{_escape(stage.stage_id)}</span></div></details>"
            "</article>"
        )

    artifact_cards = []
    for artifact in story.artifacts:
        proves = "; ".join(artifact.proves) or "没有登记可支持的正向结论。"
        boundary = "; ".join(artifact.does_not_prove) or "没有登记结论边界。"
        deletion = artifact.deletion_impact or "没有登记删除影响。"
        consumers = ", ".join(artifact.semantic_consumers) or "没有登记"
        artifact_cards.append(
            "<article class='evidence-artifact'>"
            "<div class='artifact-head'><div>"
            f"<span>{_escape(_display_value(artifact.evidence_level))}</span>"
            f"<h4>{_escape(_ARTIFACT_TITLES.get(artifact.kind, artifact.kind))}</h4>"
            "</div>"
            f"{_badge(artifact.evidence_level, _tone_for_evidence_state(artifact.evidence_level))}</div>"
            f"<p><b>可以证明：</b>{_escape(_translate_evidence_text(proves))}</p>"
            f"<p class='boundary'><b>不能证明：</b>{_escape(_translate_evidence_text(boundary))}</p>"
            "<details><summary>文件血缘与维护属性</summary>"
            f"<code>{_escape(artifact.relative_path)}</code>"
            f"<span>生产者：{_escape(artifact.producer_symbol)}</span>"
            f"<span>消费者：{_escape(consumers)}</span>"
            f"<span>{artifact.byte_size} 字节；可重建：{_display_value(artifact.rebuildable)}</span>"
            f"<span>删除影响：{_escape(_translate_evidence_text(deletion))}</span>"
            "</details></article>"
        )
    artifact_content = (
        "".join(artifact_cards)
        if artifact_cards
        else "<div class='empty-inline'>标准运行清单中没有登记产物。</div>"
    )

    ladder_details = {
        "candidate": "只证明产生了候选结果，不代表验证通过",
        "local": "只代表本地检查，不等于官方评测",
        "official": "最终正确性只能由官方评测确认",
    }
    ladder_titles = {
        "candidate": "候选结果",
        "local": "本地验证",
        "official": "官方评测",
    }
    ladder = "".join(
        _claim_step(
            ladder_titles[level],
            str(story.evidence_ladder.get(level, "unknown")),
            ladder_details[level],
            _tone_for_evidence_state(str(story.evidence_ladder.get(level, "unknown"))),
        )
        for level in ("candidate", "local", "official")
    )
    return (
        "<section class='evidence-section run-story'>"
        "<div class='section-title'><h3>运行全链路</h3>"
        "<span>结论 → 阶段 → 原始证据</span></div>"
        "<div class='run-facts'>"
        f"<span>运行 <b class='mono'>{_escape(story.run_id or '-')}</b></span>"
        f"<span>停止原因 <b>{_escape(_display_value(story.stop_reason or '-'))}</b></span>"
        "</div>"
        "<div class='section-title run-story-subtitle'><h4>当前证据最多支持什么结论</h4>"
        "<span>有文件不等于任务成功</span></div>"
        f"<div class='claim-ladder run-story-ladder'>{ladder}</div>"
        "<div class='section-title run-story-subtitle'><h4>主链阶段</h4>"
        "<span>默认隐藏模块名和事件计数</span></div>"
        f"<div class='story-stage-list'>{''.join(stage_cards)}</div>"
        "<details class='drilldown artifact-drilldown'><summary>"
        f"查看全部 {len(story.artifacts)} 个产物的内容边界与文件血缘</summary>"
        f"<div class='evidence-artifact-grid'>{artifact_content}</div></details>"
        "<details class='provenance'><summary>标准证据来源</summary>"
        f"<code>{_escape(str(manifest_path or '未找到'))}</code></details>"
        "</section>"
    )


def _tone_for_evidence_state(state: str) -> str:
    normalized_evidence_state = state.lower()
    if normalized_evidence_state == "present":
        return "ok"
    if normalized_evidence_state in {"absent", "failed", "invalid"}:
        return "bad"
    return "neutral"


def _claim_step(title: str, state: str, detail: str, tone: str) -> str:
    return (
        f"<div class='claim-step {tone}'><span>{_escape(title)}</span>"
        f"<strong>{_escape(_display_value(state))}</strong><small>{_escape(detail)}</small></div>"
    )


def _render_role_decision_rows(summary: dict[str, Any]) -> str:
    rows = []
    for result in summary.get("role_results") or []:
        excerpt = str(result.get("final_answer") or result.get("output") or "")
        excerpt = " ".join(excerpt.replace("#", " ").split())[:360]
        decision = str(result.get("decision") or result.get("status") or "-")
        rows.append(
            "<tr>"
            f"<td><b>{_escape(_display_value(result.get('role') or result.get('name') or '-'))}</b></td>"
            f"<td>{_badge(_display_value(decision), _tone_for_status(decision))}</td>"
            f"<td>{_escape(result.get('round_index', 0))}</td>"
            f"<td>{_escape(excerpt or '-')}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='4'>本次运行没有观测到角色决策。</td></tr>"


def _render_artifact_cards(summary: dict[str, Any]) -> str:
    artifacts = summary.get("artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        return "<div class='empty-inline'>本次运行没有生成多 Agent 产物。</div>"
    consumers = {
        "Implementer": "Reviewer",
        "Reviewer": "Coordinator + Verifier",
        "Verifier": "Coordinator",
        "Coordinator": "Run result",
    }
    cards = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        role = str(artifact.get("role") or "Unknown")
        path = Path(str(artifact.get("path") or ""))
        content = ""
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
        excerpt = " ".join(
            (content or str(artifact.get("summary") or "")).replace("#", " ").split()
        )[:460]
        cards.append(
            "<article class='artifact-card'>"
            f"<div class='artifact-head'><div><span>{_escape(_display_value(role))}</span><h4>{_escape(artifact.get('kind') or artifact.get('id') or '产物')}</h4></div>"
            f"{_badge('第 ' + str(artifact.get('round_index', 0)) + ' 轮', 'neutral')}</div>"
            f"<p>{_escape(excerpt or '没有可展示的内容摘要。')}</p>"
            f"<div class='artifact-handoff'><span>生产者 <b>{_escape(_display_value(role))}</b></span>"
            f"<span>消费者 <b>{_escape(_display_value(consumers.get(role, 'next stage')))}</b></span></div>"
            f"<details><summary>来源</summary><code>{_escape(str(path) if path else '未找到')}</code></details>"
            "</article>"
        )
    return "<div class='artifact-grid'>" + "".join(cards) + "</div>"


def _render_runtime_controls(project_dir: Path) -> str:
    # Lab 1 有独立指针时优先回放；普通 `forge run` 仍可查看自身控制证据。
    trace_path = _latest_governed_trace_path(project_dir) or _latest_trace_path(
        project_dir
    )
    trace = _read_json_file(trace_path)
    if not trace:
        return _empty_evidence("当前运行没有可用于展示 Runtime 控制面的 Trace 证据。")
    events = _event_list(trace)
    checkpoint = _last_event(trace, "task_state_checkpoint")
    task_state_value = checkpoint.get("task_state")
    task_state: dict[str, Any] = (
        task_state_value if isinstance(task_state_value, dict) else {}
    )
    metadata_value = task_state.get("metadata")
    metadata: dict[str, Any] = (
        metadata_value if isinstance(metadata_value, dict) else {}
    )
    environment_value = metadata.get("execution_environment")
    environment: dict[str, Any] = (
        environment_value if isinstance(environment_value, dict) else {}
    )
    context_events = [
        event for event in events if event.get("event_type") == "context_assembly"
    ]
    final_context_event = context_events[-1] if context_events else {}
    # 最终回答轮会主动关闭工具。控制面应回放最后一次真实暴露工具的快照，
    # 不能把正常的空集合误报成“工具从未被观测”。
    context_event = next(
        (
            event
            for event in reversed(context_events)
            if ((event.get("context") or event).get("tool_routing") or {}).get(
                "allowed_tools"
            )
            or ((event.get("context") or event).get("tool_routing") or {}).get(
                "dropped_tools"
            )
        ),
        final_context_event,
    )
    context_value = context_event.get("context")
    context_snapshot: dict[str, Any] = (
        context_value if isinstance(context_value, dict) else context_event
    )
    routing_value = context_snapshot.get("tool_routing")
    routing: dict[str, Any] = routing_value if isinstance(routing_value, dict) else {}
    allowed = _string_items(routing.get("allowed_tools"))
    hidden = _string_items(routing.get("dropped_tools") or routing.get("hidden_tools"))
    final_context_value = final_context_event.get("context")
    final_context: dict[str, Any] = (
        final_context_value
        if isinstance(final_context_value, dict)
        else final_context_event
    )
    final_routing_value = final_context.get("tool_routing")
    final_routing: dict[str, Any] = (
        final_routing_value if isinstance(final_routing_value, dict) else {}
    )
    final_answer_tools_closed = bool(
        context_event
        and final_context_event
        and context_event is not final_context_event
        and not _string_items(final_routing.get("allowed_tools"))
    )
    permission_events = [
        event for event in events if event.get("event_type") == "permission_check"
    ]
    decisions = {"allow": 0, "ask": 0, "deny": 0}
    for event in permission_events:
        permission_value = event.get("permission")
        permission: dict[str, Any] = (
            permission_value if isinstance(permission_value, dict) else {}
        )
        decision = str(
            event.get("permission_decision")
            or event.get("decision")
            or permission.get("decision")
            or ""
        ).lower()
        if decision in decisions:
            decisions[decision] += 1
    checkpoint_events = [
        event for event in events if event.get("event_type") == "task_state_checkpoint"
    ]
    checkpoints = len(checkpoint_events)
    checkpoint_statuses: dict[str, int] = {}
    for event in checkpoint_events:
        saved_state = event.get("task_state")
        status = str(
            saved_state.get("status") if isinstance(saved_state, dict) else "unknown"
        )
        checkpoint_statuses[status] = checkpoint_statuses.get(status, 0) + 1
    task_state_dir = trace_path.parent / "task_state" if trace_path else None
    current_state_files = (
        len(list(task_state_dir.glob("*.json")))
        if task_state_dir is not None and task_state_dir.is_dir()
        else 0
    )
    human_events = sum(
        1 for event in events if "human" in str(event.get("event_type") or "")
    )
    human_response_event = _last_event(trace, "human_input_response_loaded")
    human_request_value = human_response_event.get("request")
    human_request: dict[str, Any] = (
        human_request_value if isinstance(human_request_value, dict) else {}
    )
    selected_answer = str(human_request.get("answer") or "未观测")
    approval_event = _last_event(trace, "human_approval")
    approval_request_value = approval_event.get("approval_request")
    approval_request: dict[str, Any] = (
        approval_request_value if isinstance(approval_request_value, dict) else {}
    )
    approval_arguments_value = approval_request.get("arguments")
    approval_arguments: dict[str, Any] = (
        approval_arguments_value if isinstance(approval_arguments_value, dict) else {}
    )
    approval_status = str(
        approval_request.get("status") or approval_event.get("observation") or "未观测"
    )
    approval_target = str(approval_arguments.get("path") or "未观测")
    approval_operation_key = str(approval_request.get("operation_key") or "")
    validation_event = _last_event(trace, "validation_evidence")
    validation_value = validation_event.get("validation")
    validation: dict[str, Any] = (
        validation_value if isinstance(validation_value, dict) else {}
    )
    validation_status = str(validation.get("status") or "未执行")
    validation_kind = str(validation.get("kind") or "focused test")
    governed_decision_observed = bool(
        human_response_event or approval_event or validation_event
    )
    recovery_events = sum(
        1 for event in events if "recovery" in str(event.get("event_type") or "")
    )
    operation_events = sum(
        1 for event in events if "operation" in str(event.get("event_type") or "")
    )
    skill_event = _last_event(trace, "skill_selection")
    skill_records = [
        item for item in (skill_event.get("skills") or []) if isinstance(item, dict)
    ]
    active_skills = (
        context_snapshot.get("active_skills")
        or skill_event.get("selected_skills")
        or [f"{item.get('name')}@{item.get('version')}" for item in skill_records]
        or []
    )
    skill_activation_lines = [
        (
            f"{item.get('name')}@{item.get('version')} · "
            f"{_translate_runtime_summary(str(item.get('selection_reason') or '未记录选择原因'))} · "
            f"必需工具 {len(item.get('required_tools') or [])} / "
            f"可选工具 {len(item.get('optional_tools') or [])}"
        )
        for item in skill_records
    ]
    skill_resource_lines = [
        (
            f"{resource.get('path')} · 披露 {resource.get('disclosed_chars', 0)} / "
            f"{resource.get('original_chars', 0)} 字符 · "
            f"SHA {str(resource.get('sha256') or '')[:12]}"
            + (" · 已按预算裁剪" if resource.get("truncated") else " · 完整披露")
        )
        for item in skill_records
        for resource in (item.get("resources") or [])
        if isinstance(resource, dict)
    ]
    context_window_event = _last_event(trace, "context_window")
    context_window_value = context_window_event.get("context_window")
    context_window: dict[str, Any] = (
        context_window_value if isinstance(context_window_value, dict) else {}
    )
    memory_event = _last_event(trace, "memory_recall")
    memory_value = memory_event.get("memory")
    memory_recall: dict[str, Any] = (
        memory_value if isinstance(memory_value, dict) else {}
    )
    memory_snapshot_lines = [
        f"{scope} · {key} · revision {revision}"
        for key, scope, revision in zip(
            memory_recall.get("keys") or [],
            memory_recall.get("scopes") or [],
            memory_recall.get("revisions") or [],
        )
    ]
    llm_events = [event for event in events if event.get("event_type") == "llm_call"]
    normalization_repairs = [
        str(repair)
        for event in llm_events
        for repair in ((event.get("response_normalization") or {}).get("repairs") or [])
    ]
    burst_event = _last_event(trace, "tool_calls_bounded")
    burst_value = burst_event.get("tool_call_budget")
    burst: dict[str, Any] = burst_value if isinstance(burst_value, dict) else {}
    routing_metadata = routing.get("metadata")
    routing_metadata = routing_metadata if isinstance(routing_metadata, dict) else {}
    run_request = _read_json_file(
        trace_path.parent / "run_request.json" if trace_path else None
    )
    run_config_value = run_request.get("config")
    run_config: dict[str, Any] = (
        run_config_value if isinstance(run_config_value, dict) else {}
    )
    task_description = str(
        trace.get("task")
        or run_request.get("task")
        or task_state.get("task")
        or "执行一次需要人工批准的受治理写操作，并在恢复后完成验证。"
    )
    mcp_config_file = str(run_config.get("mcp_config_file") or "").strip()
    configured_mcp_tools = _string_items(run_config.get("mcp_allowed_tools"))
    mcp_tools = [
        tool
        for tool in allowed
        if (
            tool in configured_mcp_tools
            or str((routing_metadata.get(tool) or {}).get("mode") or "") == "mcp_style"
        )
    ]
    if not mcp_config_file:
        mcp_evidence = _render_fact_list(
            [], empty_message="本次未配置 MCP Server（不适用）"
        )
    elif mcp_tools:
        mcp_evidence = _render_fact_list(
            mcp_tools, empty_message="已配置，但本次没有暴露 MCP 工具"
        )
    else:
        mcp_evidence = _render_fact_list(
            [], empty_message="已配置 MCP Server，但本次没有向模型暴露工具"
        )
    permission_lines = [
        _translate_runtime_summary(part.strip())
        for part in str(context_snapshot.get("permission_summary") or "").split(";")
        if part.strip()
    ]
    routing_note = (
        f"取 Step {context_event.get('step', 0)} 的最后一次真实工具暴露快照。"
    )
    if final_answer_tools_closed:
        routing_note += " 最终回答轮按设计关闭了工具调用。"
    checkpoint_status_lines = [
        f"{_display_value(status)}：{count} 次写入"
        for status, count in sorted(checkpoint_statuses.items())
    ]
    mode = str(environment.get("mode") or "not_observed")
    network = str(environment.get("network_policy") or "not_observed")
    intervention_rows = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        permission_decision = str(event.get("permission_decision") or "")
        if event_type == "permission_check" and permission_decision not in {
            "ask",
            "deny",
        }:
            continue
        if event_type not in {
            "permission_check",
            "human_input_response_loaded",
            "human_approval",
            "recovery_decision",
            "validation_evidence",
        }:
            continue
        tool_call = str(event.get("tool_call") or "-")
        if event_type == "human_input_response_loaded":
            request_value = event.get("request")
            request = request_value if isinstance(request_value, dict) else {}
            state = str(request.get("status") or "responded")
            tool_call = "ask_human"
            evidence = f"人工选择：{request.get('answer') or '未记录'}"
        elif event_type == "human_approval":
            request_value = event.get("approval_request")
            request = request_value if isinstance(request_value, dict) else {}
            request_arguments_value = request.get("arguments")
            request_arguments = (
                request_arguments_value
                if isinstance(request_arguments_value, dict)
                else {}
            )
            state = str(request.get("status") or event.get("observation") or "observed")
            tool_call = str(request.get("tool_name") or tool_call)
            evidence = (
                f"目标：{request_arguments.get('path') or '未记录'}；"
                f"Operation Key：{str(request.get('operation_key') or '')[:12]}"
            )
        elif event_type == "validation_evidence":
            validation_value = event.get("validation")
            event_validation = (
                validation_value if isinstance(validation_value, dict) else {}
            )
            state = str(event_validation.get("status") or "observed")
            tool_call = str(event_validation.get("tool") or tool_call)
            raw_evidence = str(event_validation.get("evidence") or "")
            evidence = _compact_timeline_text(
                " · ".join(
                    line.strip() for line in raw_evidence.splitlines() if line.strip()
                ),
                max_chars=220,
            )
        else:
            state = permission_decision or str(
                event.get("observation") or event.get("failure_kind") or "observed"
            )
            evidence = str(
                event.get("reason")
                or event.get("recovery_hint")
                or event.get("observation")
                or ""
            )
        intervention_rows.append(
            "<tr>"
            f"<td>{_escape(event.get('step', 0))}</td>"
            f"<td>{_escape(event.get('agent_name') or '-')}</td>"
            f"<td>{_escape(_TRACE_EVENT_LABELS.get(event_type, event_type))}</td>"
            f"<td>{_badge(state, _tone_for_status(state))}</td>"
            f"<td>{_escape(tool_call)}</td>"
            f"<td>{_escape(_translate_runtime_summary(evidence))}</td>"
            "</tr>"
        )
    intervention_html = (
        "".join(intervention_rows)
        or "<tr><td colspan='6'>本次运行没有观测到人工决策、审批或验证介入。</td></tr>"
    )
    governed_decision_html = ""
    if governed_decision_observed:
        validation_description = (
            f"{validation_kind} · {validation_status}"
            if validation_event
            else "拒绝后安全结束，未执行测试"
        )
        governed_decision_html = (
            "<section class='evidence-section'><div class='section-title'>"
            "<h3>本次人工决策链</h3><span>从按钮动作到验证结果</span></div>"
            "<div class='capability-strip'>"
            f"<div><b>{_escape(_compact_timeline_text(selected_answer, max_chars=80))}</b>"
            "<span>人工选择</span></div>"
            f"<div><b>{_badge(approval_status, _tone_for_status(approval_status))}</b>"
            f"<span>补丁审批 · {_escape(approval_target)}</span></div>"
            f"<div><b>{_badge(validation_status, _tone_for_status(validation_status))}</b>"
            f"<span>{_escape(validation_description)}</span></div>"
            "</div>"
            f"<p class='boundary-note'><strong>Operation Key：</strong> "
            f"<code>{_escape(approval_operation_key or '未生成')}</code>。"
            "该键将审批决定、目标文件指纹与后续真实写入绑定；"
            "拒绝时写操作不会越过执行边界。</p></section>"
        )
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>受治理单 Agent</span><h2>Runtime 控制面</h2></div>"
        f"{_badge(mode, _tone_for_status(mode))}</div>",
        _render_lab_brief(
            question=(
                "写操作需要人工授权时，Runtime 能否在持久状态改变前停稳，"
                "持久化状态，并在批准后只执行一次？"
            ),
            input_label="本次 Task",
            input_items=[task_description],
            mechanism=(
                "入口控制 → 执行决策（操作状态 + 权限 + 人工授权）→ 受限执行 → "
                "结果与恢复（Observation + Checkpoint）"
            ),
            success_criteria=(
                "未批准不写入；批准后从已保存状态继续；恢复时拒绝重复或目标已漂移的写操作；"
                "最终 Trace、任务状态和文件结果一致。"
            ),
            boundary=(
                "这里验证 Runtime 控制语义和本地结果，不评价模型代码能力，"
                "也不把本地验证外推成官方 Benchmark 解决。"
            ),
        ),
        _metric_grid(
            [
                (
                    "执行环境",
                    _display_value(mode),
                    "本地目录 / 隔离工作树 / 容器",
                    "neutral",
                ),
                (
                    "网络策略",
                    _display_value(network),
                    "执行环境的网络边界",
                    "ok" if network == "deny" else "warn",
                ),
                (
                    "工具可见面",
                    f"{len(allowed)} 个可见",
                    f"{len(hidden)} 个隐藏",
                    "neutral",
                ),
                (
                    "权限决策",
                    f"{decisions['allow']} / {decisions['ask']} / {decisions['deny']}",
                    "允许 / 询问 / 拒绝",
                    "neutral",
                ),
                (
                    "Checkpoint 写入",
                    str(checkpoints),
                    f"覆盖写入 {current_state_files} 个当前状态文件",
                    "ok" if checkpoints else "neutral",
                ),
                (
                    "人工介入 / 恢复",
                    f"{human_events} / {recovery_events}",
                    "Trace 中实际观测到的事件",
                    "neutral",
                ),
            ]
        ),
        governed_decision_html,
        "<details class='drilldown'><summary>查看安全、工具可见性与写入边界</summary>"
        "<div class='drilldown-body'><div class='section-title'><h3>真实生效的边界</h3><span>只展示观测事实</span></div>",
        "<table><thead><tr><th>控制点</th><th>最新证据</th><th>负责模块</th></tr></thead><tbody>",
        f"<tr><td>执行隔离</td><td>{_escape(environment.get('active_workspace') or _display_value(mode))}</td><td>ExecutionEnvironment</td></tr>",
        f"<tr><td>网络策略</td><td>{_escape(_display_value(network))}</td><td>ExecutionEnvironment + CommandPolicy</td></tr>",
        f"<tr><td>工作区写操作</td><td>{_render_fact_list(permission_lines, empty_message='Trace 中没有权限摘要')}</td><td>WorkspaceSandbox + PermissionPolicy</td></tr>",
        f"<tr><td>可见工具</td><td>{_render_fact_list(allowed, empty_message='该轮向模型暴露 0 个工具')}</td><td>ToolRouter</td></tr>",
        f"<tr><td>隐藏工具</td><td>{_render_fact_list(hidden, empty_message='Router 已观测：本轮隐藏 0 个候选工具')}</td><td>ToolRouter</td></tr>",
        f"<tr><td>Skill 激活</td><td>{_render_fact_list(skill_activation_lines or active_skills, empty_message='Skill 选择已执行：本次激活 0 个')}</td><td>SkillRegistry：metadata 发现 → SKILL.md 激活</td></tr>",
        f"<tr><td>Skill 资源披露</td><td>{_render_fact_list(skill_resource_lines, empty_message='本次没有匹配参考资源；不会为完整性强塞正文')}</td><td>SkillRegistry：每个 Run 最多披露 1 份有界资源</td></tr>",
        f"<tr><td>MCP 工具暴露</td><td>{mcp_evidence}</td><td>MCP Adapter + ToolRegistry</td></tr>",
        f"<tr><td>人工控制屏障</td><td>{human_events} 个已观测事件</td><td>HumanInputStore + ApprovalStore</td></tr>",
        f"<tr><td>写操作防重复</td><td>{operation_events} 个操作状态事件</td><td>OperationLedger</td></tr>",
        f"<tr><td>Checkpoint 触发状态</td><td>{_render_fact_list(checkpoint_status_lines, empty_message='本次没有写入 Checkpoint')}</td><td>TaskStateRepository</td></tr>",
        "<tr><td>类型化证据契约</td><td>TraceEvent 信封 + 具名任务 Checkpoint</td><td>TraceRecorder + TaskCheckpoint</td></tr>",
        "</tbody></table>"
        f"<p class='boundary-note'><strong>工具快照：</strong> {_escape(routing_note)}</p>"
        "<p class='boundary-note'><strong>MCP 与本地 Tool：</strong> 对模型都是同一份 Tool Schema；本地 Tool 在进程内执行，MCP Tool 通过协议适配器调用外部进程或服务。</p>"
        "</div></details>",
        "<details class='drilldown'><summary>查看上下文、记忆与模型适配信号</summary>"
        "<div class='drilldown-body'><div class='section-title'><h3>上下文、记忆与模型适配</h3><span>来自最新 Trace</span></div>",
        "<table><thead><tr><th>信号</th><th>观测值</th><th>来源</th></tr></thead><tbody>",
        (
            "<tr><td>上下文窗口</td>"
            f"<td>是否压缩：{_escape(_display_value(context_window.get('compacted', False)))}；"
            f"Token：{_escape(context_window.get('estimated_tokens_before', 0))} → "
            f"{_escape(context_window.get('estimated_tokens_after', 0))}</td>"
            f"<td class='mono'>{_escape(context_window.get('source_hash') or '原始会话历史')}</td></tr>"
        ),
        (
            "<tr><td>长期记忆</td>"
            f"<td>召回 {_escape(memory_recall.get('recalled_count', 0))} 条；"
            f"{_render_fact_list(memory_snapshot_lines, empty_message='本 Run 没有召回长期记忆')}</td>"
            f"<td class='mono'>snapshot {_escape(str(memory_recall.get('snapshot_sha256') or '未记录')[:12])}</td></tr>"
        ),
        (
            "<tr><td>工具调用修复</td>"
            f"<td>{_escape(normalization_repairs or '本次未观测')}</td>"
            f"<td>{len(llm_events)} 次模型调用</td></tr>"
        ),
        (
            "<tr><td>单轮工具调用上限</td>"
            f"<td>上限：{_escape(burst.get('limit', '本次未触发'))}；"
            f"丢弃调用：{_escape(burst.get('dropped') or [])}</td>"
            "<td>ToolExecutionPipeline Trace</td></tr>"
        ),
        "</tbody></table><p class='boundary-note'>记忆 ID 和摘要哈希用于追溯来源；派生摘要不会冒充原始数据。</p></div></details>",
        "<section class='evidence-section'><div class='section-title'><h3>持久化与人工控制</h3><span>当前运行的事件覆盖</span></div>",
        "<div class='capability-strip'>"
        f"<div><b>{checkpoints}</b><span>Checkpoint 写入事件</span></div>"
        f"<div><b>{human_events}</b><span>人工输入事件</span></div>"
        f"<div><b>{recovery_events}</b><span>恢复决策</span></div>"
        f"<div><b>{len(permission_events)}</b><span>权限检查</span></div>"
        "</div>"
        f"<p class='boundary-note'><strong>Checkpoint 口径：</strong> 本次 Trace 记录 {checkpoints} 次关键状态写入，当前由 {current_state_files} 个 JSON 文件保存每个任务的最新可恢复状态。它按创建、等待审批、恢复、工具结果和结束等状态转换写入，不是每个 Turn 固定写一次。</p>"
        "<p class='boundary-note'>数值为 0 表示本次运行没有触发该能力，不会伪造成“通过”。</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>人工决策与验证时间线</h3><span>回答、审批、执行和恢复证据</span></div>"
        "<table><thead><tr><th>轮次</th><th>Agent</th><th>事件</th><th>状态</th><th>工具</th><th>证据</th></tr></thead>"
        f"<tbody>{intervention_html}</tbody></table></section>",
        f"<details class='provenance'><summary>控制面证据来源</summary><code>{_escape(str(trace_path))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_orchestration_dashboard(project_dir: Path) -> str:
    fanout_path = _latest_orchestration_fanout_path(project_dir)
    fanout = _read_json_file(fanout_path)
    if fanout:
        return _render_fanout_result_summary(fanout, fanout_path)
    summary_path = _latest_orchestration_summary_path(project_dir)
    summary = _read_json_file(summary_path)
    if not summary:
        return _empty_evidence("尚未找到多 Agent 编排产物，请先执行并行协同运行。")
    decisions = summary.get("role_results") or []
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>并行多 AGENT</span><h2>多 Agent 编排证据</h2></div>"
        f"{_badge(str(summary.get('status') or 'unknown'), _tone_for_status(str(summary.get('status') or '')))}</div>",
        _metric_grid(
            [
                ("编排模式", "顺序角色链", "通过显式产物交接", "neutral"),
                ("角色数量", str(len(decisions)), "实现 / 审查 / 验证", "neutral"),
                (
                    "修订轮次",
                    str(summary.get("revision_rounds", 0)),
                    "受上限约束的循环",
                    "neutral",
                ),
                (
                    "产物数量",
                    str(len(summary.get("artifacts") or [])),
                    "角色间显式交接",
                    "ok",
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>协同关系</h3><span>本次运行采用顺序角色链</span></div>"
        "<div class='coordination-graph'><div><b>实现者</b><span>候选改动 + 证据</span></div>"
        "<i>产物</i><div><b>审查者</b><span>风险 + 修订判断</span></div>"
        "<i>产物</i><div><b>验证者</b><span>独立验证</span></div>"
        "<i>结论</i><div><b>协调器</b><span>结束或进入修订</span></div></div></section>",
        "<section class='evidence-section'><div class='section-title'><h3>角色结果</h3><span>决策与证据摘要</span></div>"
        "<table><thead><tr><th>角色</th><th>决策</th><th>轮次</th><th>证据摘要</th></tr></thead>"
        f"<tbody>{_render_role_decision_rows(summary)}</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>产物交接</h3><span>直接查看角色交付内容</span></div>"
        f"{_render_artifact_cards(summary)}</section>",
        "<section class='evidence-section'><div class='section-title'><h3>三种执行模式的边界</h3><span>支持某模式，不等于本次运行已触发</span></div>"
        "<table><thead><tr><th>模式</th><th>本次证据</th><th>Runtime 契约</th></tr></thead><tbody>"
        "<tr><td>单 Agent</td><td>配对对比中已观测</td><td>标准 AgentLoop，协调开销最低</td></tr>"
        "<tr><td>顺序多 Agent</td><td>本次已观测</td><td>角色隔离、产物交接、受限修订</td></tr>"
        "<tr><td>并行 Fanout</td><td>代码支持，本次未执行</td><td>DAG 校验、Worktree Worker、改动范围门禁、确定性合并、隔离 Finalizer、选择性恢复</td></tr>"
        "</tbody></table></section>",
        f"<details class='provenance'><summary>编排证据来源</summary><code>{_escape(str(summary_path))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _quality_metric(metrics: dict[str, Any], *names: str) -> int:
    """读取新旧摘要字段；零值也是有效证据，不能被 truthy fallback 覆盖。"""

    for name in names:
        value = metrics.get(name)
        if value is not None:
            return int(value)
    return 0


def _quality_selection_incident_overview_facts(
    artifact: dict[str, Any],
) -> tuple[list[tuple[str, str, str, str]], str]:
    """只展示完整性与污染范围，不展示候选的局部正确率。"""

    incident = _mapping(artifact.get("incident"))
    decision = _mapping(artifact.get("decision"))
    planned = int(incident.get("planned_case_starts") or 0)
    before_tail = int(incident.get("slots_before_uniformly_contaminated_tail") or 0)
    affected = int(incident.get("rate_limit_affected_case_slots") or 0)
    clean = int(incident.get("rate_limit_free_case_slots") or 0)
    tail = int(incident.get("uniformly_contaminated_tail_slots") or 0)
    exit_code = decision.get("summarizer_exit_code")
    return (
        [
            (
                "预注册启动",
                str(planned),
                "两候选、交错分片、串行执行",
                "neutral",
            ),
            (
                "全量污染尾段之前",
                f"{before_tail}/{planned}",
                "20/20 均有 finalized artifact；此前 14 槽中已有 3 槽限流",
                "warn",
            ),
            (
                "Rate-limit 影响",
                f"{affected}/{planned}",
                f"仅 {clean}/{planned} 无 rate-limit 记录",
                "bad" if affected else "ok",
            ),
            (
                "全量污染尾段",
                f"{tail}/{tail}" if tail else "0",
                "两个模型均在首调用后耗尽两次 transport attempt",
                "bad" if tail else "ok",
            ),
            (
                "选择结论",
                "NO WINNER",
                f"summarizer exit {exit_code}; correctness rerun = 0",
                "warn",
            ),
        ],
        "该记录只证明选型协议正确地失败关闭；全量污染尾段之前的 14 个槽、11 个无 rate-limit 槽和任何分片子集都不得用于倒推 winner。",
    )


def _render_quality_selection_incident_dashboard(
    artifact: dict[str, Any],
    source_path: Path | None,
) -> str:
    """渲染历史事故，但不暴露任何局部正确率。"""

    incident = _mapping(artifact.get("incident"))
    decision = _mapping(artifact.get("decision"))
    source_binding = _mapping(artifact.get("source_binding"))
    planned = int(incident.get("planned_case_starts") or 0)
    before_tail = int(incident.get("slots_before_uniformly_contaminated_tail") or 0)
    affected = int(incident.get("rate_limit_affected_case_slots") or 0)
    clean = int(incident.get("rate_limit_free_case_slots") or 0)
    tail = int(incident.get("uniformly_contaminated_tail_slots") or 0)
    run_rows: list[str] = []
    for item in artifact.get("run_artifacts") or []:
        if not isinstance(item, dict):
            continue
        identity = _mapping(item.get("identity"))
        usage = _mapping(item.get("usage"))
        rate_limit = _mapping(item.get("rate_limit"))
        official = _mapping(item.get("official_aggregate_safe_counts"))
        slot_range = item.get("slot_range")
        slots = slot_range if isinstance(slot_range, list) else []
        slot_label = "–".join(str(value) for value in slots) or "未记录"
        observed = ", ".join(_string_items(identity.get("provider_reported_models")))
        stop_reasons = _mapping(item.get("stop_reasons"))
        stop_label = ", ".join(
            f"{name} × {count}" for name, count in stop_reasons.items()
        )
        total = int(official.get("total_instances") or 0)
        terminal = (
            int(official.get("completed_instances") or 0)
            + int(official.get("empty_patch_instances") or 0)
            + int(official.get("error_instances") or 0)
        )
        run_rows.append(
            "<tr>"
            f"<td>{_escape(slot_label)}</td>"
            f"<td><b>{_escape(item.get('candidate_id') or '未记录')}</b> · "
            f"{_escape(item.get('shard') or '未记录')}</td>"
            f"<td>{_escape(identity.get('provider') or '未记录')} / "
            f"{_escape(identity.get('requested_model') or '未记录')}<br>"
            f"<span class='muted'>reported: {_escape(observed or '无响应')}</span></td>"
            f"<td>{_escape(stop_label or '未记录')}<br>"
            f"<span class='muted'>rate-limit cases: "
            f"{int(rate_limit.get('affected_cases') or 0)}</span></td>"
            f"<td>{int(usage.get('llm_calls') or 0)} calls · "
            f"{int(usage.get('total_tokens') or 0):,} tokens<br>"
            f"<span class='muted'>terminal accounting {terminal}/{total}</span></td>"
            "</tr>"
        )
    run_rows_html = "".join(run_rows) or (
        "<tr><td colspan='5'>没有登记安全层 run artifact。</td></tr>"
    )
    boundaries = _string_items(artifact.get("claim_boundary"))
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>HISTORICAL INCIDENT</span>"
        f"<h2>{_escape(artifact.get('title') or 'Quality Selection Fail-Closed')}</h2></div>"
        f"{_badge('NO WINNER', 'warn')}</div>",
        "<p class='help strong'>这次尝试没有产生模型选择结论。Workbench 只展示完整性、身份、usage 与停止原因，不展示或比较局部 correctness 分数。</p>",
        _metric_grid(
            [
                ("预注册启动", str(planned), "20 planned case starts", "neutral"),
                (
                    "全量污染尾段之前",
                    f"{before_tail}/{planned}",
                    "20/20 均有 finalized artifact；第 12–14 槽已限流",
                    "warn",
                ),
                (
                    "Rate-limit 影响",
                    f"{affected}/{planned}",
                    f"无 rate-limit 记录 {clean}/{planned}",
                    "bad",
                ),
                (
                    "全量污染尾段",
                    f"{tail}/{tail}" if tail else "0",
                    "第 15–20 槽；两模型均首调用失败",
                    "bad",
                ),
                (
                    "Summarizer",
                    f"exit {decision.get('summarizer_exit_code', 'unknown')}",
                    "失败关闭；winner = null；正确性重跑 = 0",
                    "warn",
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>安全层运行清单</h3><span>usage 仅作 provenance，不作 tie-break</span></div>"
        "<table><thead><tr><th>槽位</th><th>候选 / 分片</th><th>身份</th><th>停止原因</th><th>Usage / 终态对账</th></tr></thead>"
        f"<tbody>{run_rows_html}</tbody></table>"
        "<p class='boundary-note'>终态对账只使用官方 aggregate 的 completed、empty-patch 与 error 安全计数字段；这里刻意不渲染 partial resolved 分数。</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>冻结身份与哈希</h3><span>可审计，不补跑</span></div>"
        "<table><tbody>"
        f"<tr><td>Source tag</td><td class='mono'>{_escape(source_binding.get('source_tag') or '未记录')}</td></tr>"
        f"<tr><td>Source commit</td><td class='mono'>{_escape(source_binding.get('source_commit_sha') or '未记录')}</td></tr>"
        f"<tr><td>Protocol SHA-256</td><td class='mono'>{_escape(source_binding.get('protocol_sha256') or '未记录')}</td></tr>"
        f"<tr><td>Command manifest SHA-256</td><td class='mono'>{_escape(source_binding.get('command_manifest_sha256') or '未记录')}</td></tr>"
        "</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>结论边界</h3><span>不得从局部样本倒推 winner</span></div>"
        f"{_render_fact_list(boundaries, empty_message='尚未记录结论边界')}</section>",
        "<details class='provenance'><summary>Fail-closed 事故记录来源</summary>"
        f"<code>{_escape(str(source_path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _canonical_showcase_overview_facts(
    showcase: dict[str, Any],
) -> tuple[list[tuple[str, str, str, str]], str]:
    """只展示已冻结或已裁决事实；缺失结果必须保持为待运行。"""

    profile = _mapping(showcase.get("current_profile"))
    evaluation = _mapping(showcase.get("canonical_evaluation"))
    planned = int(evaluation.get("planned") or 0)
    completed_value = evaluation.get("completed")
    completed = int(completed_value) if completed_value is not None else None
    evaluation_label = str(evaluation.get("label") or "Canonical-50")
    selected_model = str(profile.get("selected_model") or "").strip()
    profile_frozen = bool(profile.get("frozen"))
    candidates = _string_items(profile.get("model_candidates"))
    model_context = (
        f"{len(candidates)} 个预注册候选" if candidates else "已完成观测使用的模型身份"
    )
    return (
        [
            (
                "当前质量配置",
                str(profile.get("profile_id") or "待命名"),
                "已冻结" if profile_frozen else "候选比较尚未冻结",
                "ok" if profile_frozen else "warn",
            ),
            (
                "最终模型",
                selected_model if selected_model and profile_frozen else "待选择",
                model_context,
                "ok" if selected_model and profile_frozen else "warn",
            ),
            (
                f"{evaluation_label}进度",
                _pending_progress(completed, planned),
                _display_value(evaluation.get("sample_kind") or "固定样本"),
                "ok" if planned and completed == planned else "warn",
            ),
            (
                "Official Pass@1",
                _canonical_score(showcase),
                "配置与协议冻结、终态完整计入且证据校验后发布",
                "ok" if _canonical_score_is_publishable(showcase) else "warn",
            ),
        ],
        str(
            evaluation.get("claim")
            or "结论只属于该固定样本，不代表完整 SWE-bench Verified。"
        ),
    )


def _render_canonical_showcase_dashboard(
    showcase: dict[str, Any],
    source_path: Path | None,
) -> str:
    """渲染当前展示面；开发集与健康检查永远不冒充质量分数。"""

    profile = _mapping(showcase.get("current_profile"))
    references = _mapping(profile.get("references"))
    evaluation = _mapping(showcase.get("canonical_evaluation"))
    planned = int(evaluation.get("planned") or 0)
    completed_value = evaluation.get("completed")
    completed = int(completed_value) if completed_value is not None else None
    terminal_value = evaluation.get("terminal_accounted")
    terminal = int(terminal_value) if terminal_value is not None else None
    evaluation_label = str(evaluation.get("label") or "Canonical-50")
    selected_model = str(profile.get("selected_model") or "").strip()
    profile_frozen = bool(profile.get("frozen"))
    candidate_items = _string_items(profile.get("model_candidates"))
    candidate_section = (
        "<h4>候选记录</h4>"
        + _render_fact_list(candidate_items, empty_message="尚未登记模型候选")
        if candidate_items
        else ""
    )
    support_rows: list[str] = []
    role_labels = {
        "development_and_regression_only": "开发与回归专用",
        "infrastructure_health_only": "基础设施健康检查专用",
        "future_confirmation_only": "未来扩大样本确认",
    }
    for item in showcase.get("supporting_checks") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "未记录")
        support_rows.append(
            "<tr>"
            f"<td><b>{_escape(item.get('label') or item.get('id') or '未命名')}</b></td>"
            f"<td>{_escape(role_labels.get(role, role))}</td>"
            f"<td>{_escape(_display_value(item.get('status') or 'not_run'))}</td>"
            "<td>否</td>"
            "</tr>"
        )
    support_rows_html = "".join(support_rows) or (
        "<tr><td colspan='4'>尚未登记辅助检查。</td></tr>"
    )
    boundaries = _string_items(showcase.get("boundaries"))
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>QUALITY SHOWCASE</span>"
        f"<h2>{_escape(showcase.get('title') or 'NanoHarness Canonical Showcase')}</h2></div>"
        f"{_badge(_display_value(showcase.get('status') or 'pending'), _tone_for_status(str(showcase.get('status') or '')))}</div>",
        "<p class='help strong'>这是当前唯一默认质量展示面：先给出已完成的 official 结果，再说明样本边界与下一轮确认实验。历史选型和失败轮次不进入主动叙事。</p>",
        _metric_grid(
            [
                (
                    "质量配置",
                    str(profile.get("profile_id") or "待命名"),
                    "已冻结" if profile_frozen else "候选比较尚未冻结",
                    "ok" if profile_frozen else "warn",
                ),
                (
                    "最终模型",
                    selected_model if selected_model and profile_frozen else "待选择",
                    (
                        f"候选 {len(candidate_items)} 个"
                        if candidate_items
                        else "已完成观测的模型身份"
                    ),
                    "ok" if selected_model and profile_frozen else "warn",
                ),
                (
                    f"{evaluation_label}完成",
                    _pending_progress(completed, planned),
                    "Pass@1；不做正确性重跑",
                    "ok" if planned and completed == planned else "warn",
                ),
                (
                    "终态证据覆盖",
                    _pending_progress(terminal, planned),
                    "官方评测、空 Patch 与基础设施终态必须完整对账",
                    "ok" if planned and terminal == planned else "warn",
                ),
                (
                    "Official Pass@1",
                    _canonical_score(showcase),
                    "只属于已声明的固定样本",
                    "ok" if _canonical_score_is_publishable(showcase) else "warn",
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>当前质量配置</h3><span>模型、协议与结果身份</span></div>"
        "<table><tbody>"
        f"<tr><td>Profile ID</td><td class='mono'>{_escape(profile.get('profile_id') or '待命名')}</td></tr>"
        f"<tr><td>选择状态</td><td>{_escape(_display_value(profile.get('status') or 'pending'))}</td></tr>"
        f"<tr><td>冻结状态</td><td>{'已冻结' if profile_frozen else '未冻结'}</td></tr>"
        f"<tr><td>最终模型</td><td>{_escape(selected_model if selected_model and profile_frozen else '待选择')}</td></tr>"
        f"<tr><td>结果集合</td><td>{_escape(references.get('selection_set') or evaluation_label)}</td></tr>"
        "</tbody></table>"
        f"{candidate_section}</section>",
        f"<section class='evidence-section'><div class='section-title'><h3>{_escape(evaluation_label)}证据</h3><span>正式展示分母</span></div>"
        "<table><tbody>"
        f"<tr><td>数据集</td><td>{_escape(evaluation.get('dataset') or '未记录')}</td></tr>"
        f"<tr><td>样本</td><td>{planned} 题 · {_escape(_display_value(evaluation.get('sample_kind') or '固定样本'))}</td></tr>"
        f"<tr><td>协议</td><td>{_escape(evaluation.get('protocol') or 'Pass@1')}</td></tr>"
        f"<tr><td>Cohort 冻结</td><td>{'已冻结' if evaluation.get('cohort_frozen') else '未冻结'}</td></tr>"
        f"<tr><td>运行协议冻结</td><td>{'已冻结' if evaluation.get('protocol_frozen') else '未冻结'}</td></tr>"
        f"<tr><td>终态计入</td><td>{_escape(_pending_progress(terminal, planned))}</td></tr>"
        f"<tr><td>Official resolved</td><td>{_escape(_display_value(evaluation.get('official_resolved')))}</td></tr>"
        f"<tr><td>Official unresolved</td><td>{_escape(_display_value(evaluation.get('official_unresolved')))}</td></tr>"
        f"<tr><td>Empty Patch</td><td>{_escape(_display_value(evaluation.get('empty_patch')))}</td></tr>"
        f"<tr><td>基础设施终态</td><td>provider {_escape(_display_value(evaluation.get('provider_infra')))} · evaluator {_escape(_display_value(evaluation.get('evaluator_infra')))}</td></tr>"
        f"<tr><td>证据校验</td><td>{'已通过' if evaluation.get('evidence_validated') is True else '待校验'}</td></tr>"
        f"<tr><td>当前状态</td><td>{_escape(_display_value(evaluation.get('status') or 'not_started'))}</td></tr>"
        "</tbody></table>"
        f"<p class='boundary-note'><strong>公开口径：</strong>{_escape(evaluation.get('claim') or '只属于该确定性样本。')}</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>辅助检查的角色</h3><span>不进入质量 headline</span></div>"
        "<table><thead><tr><th>集合</th><th>唯一用途</th><th>状态</th><th>质量分数</th></tr></thead>"
        f"<tbody>{support_rows_html}</tbody></table>"
        "<p class='boundary-note'>辅助检查不进入当前质量 headline；扩大样本后的结果必须单独满足完整分母与证据门。</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>结论边界</h3><span>机器摘要中的固定约束</span></div>"
        f"{_render_fact_list(boundaries, empty_message='尚未记录额外边界')}</section>",
        "<details class='provenance'><summary>Canonical 摘要来源</summary>"
        f"<code>{_escape(str(source_path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _pending_progress(value: int | None, planned: int) -> str:
    if value is None:
        return f"待运行 / {planned}" if planned else "待运行"
    return f"{value}/{planned}" if planned else str(value)


def _canonical_score_is_publishable(showcase: dict[str, Any]) -> bool:
    profile = _mapping(showcase.get("current_profile"))
    evaluation = _mapping(showcase.get("canonical_evaluation"))
    selected_model = profile.get("selected_model")
    planned = _canonical_nonnegative_int(evaluation.get("planned"))
    completed = _canonical_nonnegative_int(evaluation.get("completed"))
    terminal = _canonical_nonnegative_int(evaluation.get("terminal_accounted"))
    official_evaluated = _canonical_nonnegative_int(
        evaluation.get("official_evaluated")
    )
    empty_patch = _canonical_nonnegative_int(evaluation.get("empty_patch"))
    provider_infra = _canonical_nonnegative_int(evaluation.get("provider_infra"))
    evaluator_infra = _canonical_nonnegative_int(evaluation.get("evaluator_infra"))
    resolved = _canonical_nonnegative_int(evaluation.get("official_resolved"))
    return bool(
        planned
        and str(showcase.get("status") or "") == "completed"
        and str(evaluation.get("status") or "") == "completed"
        and profile.get("frozen") is True
        and isinstance(selected_model, str)
        and selected_model.strip()
        and evaluation.get("cohort_frozen") is True
        and evaluation.get("protocol_frozen") is True
        and evaluation.get("evidence_validated") is True
        and completed is not None
        and completed == planned
        and terminal is not None
        and terminal == planned
        and official_evaluated is not None
        and empty_patch is not None
        and official_evaluated + empty_patch == planned
        and provider_infra == 0
        and evaluator_infra == 0
        and resolved is not None
        and 0 <= resolved <= official_evaluated
    )


def _canonical_nonnegative_int(value: object) -> int | None:
    """Canonical 发布字段只接受 JSON 非负整数；布尔值不能冒充计数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _canonical_score(showcase: dict[str, Any]) -> str:
    if not _canonical_score_is_publishable(showcase):
        return "待完整裁决"
    evaluation = _mapping(showcase.get("canonical_evaluation"))
    return f"{int(evaluation['official_resolved'])}/{int(evaluation['planned'])}"


def _mapping(value: object) -> dict[str, Any]:
    """收窄机器摘要的可选对象，避免 schema 演进时渲染器崩溃。"""

    return value if isinstance(value, dict) else {}


def _phase2_summary(experiment: dict[str, Any]) -> dict[str, Any]:
    """schema v3 才启用 Phase 2；不影响 v1/v2 的发布故事。"""

    if int(experiment.get("schema_version") or 1) < 3:
        return {}
    return _mapping(experiment.get("phase2"))


def _optional_metric(metrics: dict[str, Any], *names: str) -> int | None:
    """读取可选整数指标；缺失与真实零值必须区分。"""

    for name in names:
        value = metrics.get(name)
        if value is not None:
            return int(value)
    return None


def _quality_result(metrics: dict[str, Any]) -> str:
    """格式化独立 cohort 的 resolved/planned；未收口时不伪造 0/N。"""

    resolved = _optional_metric(metrics, "official_resolved", "confirmed_solved")
    planned = _optional_metric(metrics, "planned", "case_count", "official_denominator")
    if resolved is None or planned is None:
        return "待运行"
    return f"{resolved}/{planned}"


def _quality_transition(
    baseline_metrics: dict[str, Any],
    treatment_metrics: dict[str, Any],
) -> str:
    return f"{_quality_result(baseline_metrics)} → {_quality_result(treatment_metrics)}"


def _quality_result_tone(metrics: dict[str, Any]) -> str:
    resolved = _optional_metric(metrics, "official_resolved", "confirmed_solved")
    planned = _optional_metric(metrics, "planned", "case_count", "official_denominator")
    if resolved is None or planned is None:
        return "warn"
    return "ok" if planned and resolved == planned else "neutral"


def _optional_ratio(numerator: int | None, denominator: int | None) -> str:
    if numerator is None or denominator is None:
        return "待运行"
    return f"{numerator}/{denominator}"


def _quality_reference_metrics(experiment: dict[str, Any]) -> dict[str, Any]:
    """返回 schema v2 的正式参考基线；旧摘要只取首轮过程事实。"""

    schema_version = int(experiment.get("schema_version") or 1)
    iterations = [
        item for item in experiment.get("iterations") or [] if isinstance(item, dict)
    ]
    if schema_version >= 2:
        value = experiment.get("reference_metrics")
        if isinstance(value, dict):
            return value
        reference_id = str(experiment.get("reference_iteration") or "R0")
    else:
        reference_id = str(iterations[0].get("id") or "") if iterations else ""
    for iteration in iterations:
        if str(iteration.get("id") or "") != reference_id:
            continue
        metrics = iteration.get("metrics")
        return metrics if isinstance(metrics, dict) else {}
    return {}


def _quality_decided(metrics: dict[str, Any], planned: int) -> int:
    """返回官方明确 resolved/failed 的数量；empty 与 infra 不进入分母。"""

    for name in ("official_decided", "decided"):
        value = metrics.get(name)
        if value is not None:
            return int(value)
    resolved_names = ("official_resolved", "confirmed_solved")
    unresolved_names = ("official_unresolved", "confirmed_unresolved")
    if any(name in metrics for name in (*resolved_names, *unresolved_names)):
        return _quality_metric(metrics, *resolved_names) + _quality_metric(
            metrics,
            *unresolved_names,
        )
    not_adjudicated = _quality_metric(metrics, "not_adjudicated")
    empty = _quality_metric(
        metrics,
        "official_empty_or_skipped",
        "official_skipped_empty_patch",
    )
    infrastructure = _quality_metric(metrics, "official_infrastructure_error")
    return max(0, planned - not_adjudicated - empty - infrastructure)


def _render_legacy_runtime_quality_dashboard(
    experiment: dict[str, Any],
    source_path: Path | None,
) -> str:
    """旧 schema 只展示探索过程，拒绝把历史 accepted 标签升级为正式结论。"""

    iterations = [
        item for item in experiment.get("iterations") or [] if isinstance(item, dict)
    ]
    rows: list[str] = []
    for index, iteration in enumerate(iterations):
        metrics_value = iteration.get("metrics")
        metrics = metrics_value if isinstance(metrics_value, dict) else {}
        planned = _quality_metric(metrics, "planned", "case_count")
        patch_generated = _quality_metric(metrics, "patch_generated")
        provider_tokens = _quality_metric(metrics, "provider_tokens", "total_tokens")
        cost = float(metrics.get("estimated_cost_usd") or 0.0)
        rows.append(
            "<tr>"
            f"<td><b>P{index}</b><small>旧 {_escape(iteration.get('id') or '')}</small></td>"
            f"<td>{_escape(iteration.get('scope') or '-')}</td>"
            f"<td>{_escape(iteration.get('change') or '-')}</td>"
            f"<td>{patch_generated}/{planned}</td>"
            f"<td>{provider_tokens:,}</td>"
            f"<td>${cost:.6f}</td>"
            f"<td>{_escape(iteration.get('decision') or '-')}（仅历史标签）</td>"
            "</tr>"
        )
    boundaries = [
        "旧摘要缺少完整 official 裁决与冻结复现协议，不展示解决率。",
        "candidate Patch、Token 和失败工具调用只能作为探索信号。",
        "旧 accepted_iteration 已撤回，正式实验必须从 schema v2 的 R0 开始。",
    ]
    empty_row = '<tr><td colspan="7">没有历史迭代。</td></tr>'
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>PRE-R0 · 探索性预实验</span>"
        f"<h2>{_escape(experiment.get('title') or '旧 Runtime 质量摘要')}</h2></div>"
        f"{_badge('exploratory_only', 'warn')}</div>",
        _render_lab_brief(
            question=str(experiment.get("question") or "旧实验留下了哪些探索信号？"),
            input_label="历史样本",
            input_items=[str(item) for item in experiment.get("case_ids") or []],
            mechanism="保留 Trace 与过程指标 → 撤回旧 accepted 标签 → 重新冻结正式 R0 协议",
            success_criteria="只用于提出假设，不证明 official resolved 提升。",
            boundary="；".join(boundaries),
        ),
        "<p class='boundary-note scope-warning'><strong>Fail closed：</strong>该文件是 schema v1。Workbench 不读取其 accepted_metrics 作为正式参考，也不显示 resolved rate。</p>",
        "<section class='evidence-section'><div class='section-title'><h3>P0-P2 历史过程</h3><span>candidate 与成本，不是解决率</span></div>"
        "<table><thead><tr><th>迁移名称</th><th>范围</th><th>变量</th><th>候选 Patch</th><th>Provider Token</th><th>估算成本</th><th>旧决策</th></tr></thead>"
        f"<tbody>{''.join(rows) or empty_row}</tbody></table></section>",
        f"<details class='provenance'><summary>旧摘要来源</summary><code>{_escape(str(source_path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_phase1_runtime_quality_dashboard(
    experiment: dict[str, Any],
    source_path: Path | None,
    *,
    retained_after_phase2: bool = False,
) -> str:
    """展示正式基线、候选决策和证据边界，不混用不同实验分母。"""

    if int(experiment.get("schema_version") or 1) < 2:
        return _render_legacy_runtime_quality_dashboard(experiment, source_path)

    reference_iteration = str(experiment.get("reference_iteration") or "R0")
    accepted_iteration = str(experiment.get("accepted_iteration") or "")
    reference_metrics = _quality_reference_metrics(experiment)
    iterations = [
        item for item in experiment.get("iterations") or [] if isinstance(item, dict)
    ]
    cases = [
        item for item in experiment.get("case_results") or [] if isinstance(item, dict)
    ]
    fixed_conditions = experiment.get("fixed_conditions") or {}
    iteration_ids = [str(item.get("id") or "") for item in iterations]
    iteration_ids = [item for item in iteration_ids if item]

    iteration_rows: list[str] = []
    for iteration in iterations:
        metrics_value = iteration.get("metrics")
        metrics = metrics_value if isinstance(metrics_value, dict) else {}
        planned = _quality_metric(
            metrics,
            "planned",
            "case_count",
            "official_denominator",
        )
        official_resolved = _quality_metric(
            metrics,
            "official_resolved",
            "confirmed_solved",
        )
        official_unresolved = _quality_metric(
            metrics,
            "official_unresolved",
            "confirmed_unresolved",
        )
        official_empty = _quality_metric(
            metrics,
            "official_empty_or_skipped",
            "official_skipped_empty_patch",
        )
        official_infra = _quality_metric(
            metrics,
            "official_infrastructure_error",
        )
        official_decided = _quality_decided(metrics, planned)
        patch_generated = int(metrics.get("patch_generated") or 0)
        runtime_steps = _quality_metric(metrics, "runtime_step_entries")
        llm_calls = _quality_metric(metrics, "llm_calls")
        total_tokens = _quality_metric(metrics, "provider_tokens", "total_tokens")
        estimated_cost = float(metrics.get("estimated_cost_usd") or 0.0)
        tool_calls = int(metrics.get("tool_calls") or 0)
        failed_tool_calls = int(metrics.get("failed_tool_calls") or 0)
        failed_tool_rate = (
            f"{failed_tool_calls}/{tool_calls} ({failed_tool_calls / tool_calls:.1%})"
            if tool_calls
            else "0/0"
        )
        decision = str(iteration.get("decision") or "pending")
        iteration_rows.append(
            "<tr>"
            f"<td><b>{_escape(iteration.get('id') or '')}</b></td>"
            f"<td>{_escape(iteration.get('cohort') or iteration.get('scope') or '-')}</td>"
            "<td>"
            f"<b>{_escape(iteration.get('hypothesis') or iteration.get('bottleneck') or '正式基线')}</b>"
            f"<span class='quality-cell-detail'>{_escape(iteration.get('change') or '无')}</span>"
            "</td>"
            "<td>"
            f"<b>{official_resolved}/{planned} resolved / planned</b>"
            f"<span class='quality-cell-detail'>decided {official_decided}/{planned} · unresolved {official_unresolved} · empty {official_empty} · infra {official_infra}</span>"
            "</td>"
            "<td>"
            f"<b>Patch {patch_generated}/{planned}</b>"
            f"<span class='quality-cell-detail'>Step / LLM {runtime_steps}/{llm_calls} · failed Tool {_escape(failed_tool_rate)}</span>"
            "</td>"
            f"<td>{total_tokens:,}<small class='quality-cell-detail'>${estimated_cost:.4f}</small></td>"
            f"<td>{_badge(decision, _tone_for_status(decision))}</td>"
            "</tr>"
        )

    case_rows: list[str] = []
    for case in cases:
        outcomes_value = case.get("iterations")
        outcomes = outcomes_value if isinstance(outcomes_value, dict) else case
        result_cells = "".join(
            f"<td>{_escape(_quality_case_result_label(outcomes.get(iteration_id)))}</td>"
            for iteration_id in iteration_ids
        )
        case_rows.append(
            "<tr>"
            f"<td class='mono'>{_escape(case.get('case_id') or '')}</td>"
            f"{result_cells}"
            f"<td>{_escape(case.get('transition') or case.get('note') or '')}</td>"
            "</tr>"
        )

    pareto_items = experiment.get("failure_pareto") or []
    pareto_rows = "".join(
        "<tr>"
        f"<td>{index}</td>"
        f"<td>{_escape(item.get('failure') or '')}</td>"
        f"<td>{int(item.get('count') or 0)}</td>"
        f"<td>{_escape(item.get('evidence') or '')}</td>"
        "</tr>"
        for index, item in enumerate(pareto_items, start=1)
        if isinstance(item, dict)
    )
    historical_value = experiment.get("historical_exploration") or []
    if isinstance(historical_value, dict):
        historical = (
            historical_value.get("iterations") or historical_value.get("runs") or []
        )
        historical_shortcomings = (
            historical_value.get("three_primary_shortcomings") or []
        )
    else:
        historical = historical_value
        historical_shortcomings = []
    historical_rows = "".join(
        "<tr>"
        f"<td>{_escape(item.get('id') or item.get('name') or '')}</td>"
        f"<td>{_escape(item.get('scope') or item.get('cohort') or '-')}</td>"
        f"<td>{_escape(item.get('finding') or item.get('reason') or item.get('legacy_decision') or item.get('decision') or item.get('change') or '')}</td>"
        f"<td>{_escape(item.get('claim_boundary') or '探索性预实验，不进入正式 R0-R3 因果链')}</td>"
        "</tr>"
        for item in historical
        if isinstance(item, dict)
    )
    historical_shortcoming_items = "".join(
        f"<li>{_escape(item)}</li>" for item in historical_shortcomings
    )
    excluded_incidents = [
        (str(iteration.get("id") or ""), iteration.get("invalid_launch_excluded"))
        for iteration in iterations
        if isinstance(iteration.get("invalid_launch_excluded"), dict)
    ]
    excluded_rows = "".join(
        "<tr>"
        f"<td>{_escape(iteration_id)}</td>"
        f"<td>{_escape(incident.get('reason') or '')}</td>"
        f"<td>{_escape(incident.get('observed_but_excluded') or '-')}</td>"
        f"<td>{int(incident.get('provider_tokens_lower_bound') or 0):,}</td>"
        f"<td>${float(incident.get('confirmed_cost_usd_lower_bound') or 0.0):.6f}</td>"
        f"<td>{_escape(_display_value(incident.get('excluded_from_all_gates_and_valid_metrics')))}</td>"
        "</tr>"
        for iteration_id, incident in excluded_incidents
        if isinstance(incident, dict)
    )
    mechanism_rows = "".join(
        "<tr>"
        f"<td>{_escape(iteration.get('id') or '')}</td>"
        f"<td>{int(check.get('context_assembly_count') or 0)}</td>"
        f"<td>{int(check.get('create_file_visible_context_count') or 0)}</td>"
        f"<td>{int(check.get('create_file_dropped_context_count') or 0)}</td>"
        f"<td>{int(check.get('create_file_action_count') or 0)}</td>"
        f"<td>{_escape(_display_value(check.get('mechanism_result') or '-'))}</td>"
        f"<td>{_escape(_display_value(check.get('task_outcome_result') or '-'))}</td>"
        "</tr>"
        for iteration in iterations
        for check in [iteration.get("mechanism_check")]
        if isinstance(check, dict)
    )
    cost_and_time_value = experiment.get("cost_and_time")
    cost_and_time = cost_and_time_value if isinstance(cost_and_time_value, dict) else {}
    observed_costs = cost_and_time.get("observed") or []
    cost_rows = "".join(
        "<tr>"
        f"<td>{_escape(item.get('iteration') or '')}</td>"
        f"<td>{_escape(item.get('cohort') or '')}</td>"
        f"<td>{int(item.get('provider_tokens') or 0):,}</td>"
        f"<td>${float(item.get('cost_usd') or 0.0):.6f}</td>"
        f"<td>{float(item.get('wall_minutes') or 0.0):.1f} min</td>"
        f"<td>{float(item.get('summed_llm_latency_minutes') or 0.0):.1f} min</td>"
        "</tr>"
        for item in observed_costs
        if isinstance(item, dict)
    )
    rollback_value = experiment.get("rollback")
    rollback = rollback_value if isinstance(rollback_value, dict) else {}
    condition_rows = "".join(
        f"<tr><td>{_escape(key)}</td><td>{_escape(_display_value(value))}</td></tr>"
        for key, value in fixed_conditions.items()
    )
    story = experiment.get("decision_story") or []
    boundaries = experiment.get("boundaries") or []
    planned = _quality_metric(
        reference_metrics,
        "planned",
        "case_count",
        "official_denominator",
    )
    resolved = _quality_metric(
        reference_metrics,
        "official_resolved",
        "confirmed_solved",
    )
    unresolved = _quality_metric(
        reference_metrics,
        "official_unresolved",
        "confirmed_unresolved",
    )
    empty = _quality_metric(
        reference_metrics,
        "official_empty_or_skipped",
        "official_skipped_empty_patch",
    )
    infrastructure = _quality_metric(
        reference_metrics,
        "official_infrastructure_error",
    )
    decided = _quality_decided(reference_metrics, planned)
    total_tokens = _quality_metric(
        reference_metrics,
        "provider_tokens",
        "total_tokens",
    )
    estimated_cost = float(reference_metrics.get("estimated_cost_usd") or 0.0)
    acceptance_text = str(
        reference_metrics.get("evaluated_patch_acceptance")
        or (f"{resolved}/{decided}" if decided else "无可裁决 Patch")
    )
    candidate_iterations = [
        item for item in iterations if str(item.get("id") or "") != reference_iteration
    ]
    accepted_candidate_count = sum(
        str(item.get("decision") or "").lower() in {"accepted", "adopted"}
        for item in candidate_iterations
    )
    rejected_candidate_count = sum(
        str(item.get("decision") or "").lower() == "rejected"
        for item in candidate_iterations
    )
    pending_candidate_count = (
        len(candidate_iterations) - accepted_candidate_count - rejected_candidate_count
    )
    if accepted_iteration:
        candidate_decision_note = f"采纳 {accepted_iteration}"
    elif (
        candidate_iterations
        and rejected_candidate_count == len(candidate_iterations)
        and rollback
    ):
        candidate_decision_note = "全部候选轮均拒绝并回滚"
    elif candidate_iterations:
        candidate_decision_note = (
            f"未采纳；rejected {rejected_candidate_count}，"
            f"pending/other {pending_candidate_count}"
        )
    else:
        candidate_decision_note = "尚无候选轮"
    status = str(experiment.get("status") or "not_run")
    empty_iteration_row = '<tr><td colspan="7">尚无迭代结果。</td></tr>'
    empty_pareto_row = '<tr><td colspan="4">没有失败样本。</td></tr>'
    empty_historical_row = '<tr><td colspan="4">没有迁移的历史预实验。</td></tr>'
    empty_excluded_row = '<tr><td colspan="6">没有无效启动记录。</td></tr>'
    empty_mechanism_row = '<tr><td colspan="7">没有独立机制检验。</td></tr>'
    empty_cost_row = '<tr><td colspan="6">没有成本与时间记录。</td></tr>'
    case_column_count = len(iteration_ids) + 2
    empty_case_row = f'<tr><td colspan="{case_column_count}">尚无逐题结果。</td></tr>'
    case_headers = "".join(
        f"<th>{_escape(iteration_id)}</th>" for iteration_id in iteration_ids
    )
    rollback_html = ""
    if rollback:
        rollback_html = (
            "<p class='boundary-note scope-warning'><strong>最终处置：</strong>"
            f"候选策略已在 {_escape(rollback.get('commit') or '未记录提交')} 回滚；"
            f"测量完整性修复 {_escape(rollback.get('retained_measurement_hygiene_commit') or '未记录')} 保留。"
            f"{_escape(rollback.get('reason') or '')}</p>"
        )

    phase1_kicker = (
        "PHASE 1 · 历史正式 R0-R3（完整保留）"
        if retained_after_phase2
        else "功能冻结后的正式质量实验"
    )
    phase1_iteration_heading = (
        "Phase 1 · 正式 R0-R3" if retained_after_phase2 else "正式 R0-R3"
    )
    body = [
        f"<div class='view-heading'><div><span class='view-kicker'>{phase1_kicker}</span>"
        f"<h2>{_escape(experiment.get('title') or 'Runtime 质量实验')}</h2></div>"
        f"{_badge(status, _tone_for_status(status))}</div>",
        _render_lab_brief(
            question=str(
                experiment.get("question") or "Runtime 质量是否可测量、可改进？"
            ),
            input_label="固定样本",
            input_items=[str(item) for item in experiment.get("case_ids") or []],
            mechanism="冻结 evaluator、模型、Case、工具、预算与环境 → 正式 R0 → Failure Pareto → 单假设 Sentinel → 预注册 gate → 采纳或回滚",
            success_criteria=str(
                experiment.get("success_criteria")
                or "official resolved 优先；代表性失败必须转正，正确性 guard 不得退化。"
            ),
            boundary="；".join(str(item) for item in boundaries),
        ),
        _metric_grid(
            [
                (
                    "正式参考",
                    reference_iteration,
                    "Golden-10 固定 planned 分母",
                    "neutral",
                ),
                (
                    "Official resolved / planned",
                    f"{resolved}/{planned}",
                    "正式主指标；不使用只评非空 Patch 的分母",
                    "ok" if resolved else "warn",
                ),
                (
                    "官方裁决覆盖",
                    f"{decided}/{planned}",
                    f"resolved {resolved} + unresolved {unresolved}",
                    "neutral",
                ),
                (
                    "未进入 correctness 裁决",
                    f"empty {empty} · infra {infrastructure}",
                    "empty/skipped 和 infra 都不改写为 unresolved",
                    "neutral",
                ),
                (
                    "候选策略采纳",
                    f"{accepted_candidate_count}/{len(candidate_iterations)}",
                    candidate_decision_note,
                    "warn"
                    if candidate_iterations and not accepted_candidate_count
                    else "ok",
                ),
                (
                    "R0 Provider Token",
                    f"{total_tokens:,}",
                    "DeepSeek 生成阶段；不同 Sentinel 分母不做总量横比",
                    "neutral",
                ),
                (
                    "R0 估算成本",
                    f"${estimated_cost:.4f}",
                    "只统计已发布 provider usage",
                    "neutral",
                ),
                (
                    "Evaluated-Patch acceptance",
                    acceptance_text,
                    "仅辅助诊断；绝不命名为解决率",
                    "neutral",
                ),
            ]
        ),
        "<p class='boundary-note scope-warning'><strong>分母边界：</strong>R0 是 Golden-10；R1-R3 是不同的 Sentinel 子集。表中只展示各轮 resolved / planned，禁止把百分比横向包装成 Golden-10 提升。</p>",
        f"<section class='evidence-section'><div class='section-title'><h3>{phase1_iteration_heading}</h3><span>每轮只验证一个主要假设；gate 优先于过程指标</span></div>"
        "<table><thead><tr><th>轮次</th><th>范围</th><th>假设与单一改动</th><th>官方结果</th><th>过程证据</th><th>Provider Token / 成本</th><th>决策</th></tr></thead>"
        f"<tbody>{''.join(iteration_rows) or empty_iteration_row}</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>机制生效不等于任务成功</h3><span>R3 Trace contract 与任务结果分栏</span></div>"
        "<table><thead><tr><th>轮次</th><th>Context</th><th>create_file 可见</th><th>create_file 被丢弃</th><th>实际动作</th><th>机制</th><th>任务结果</th></tr></thead>"
        f"<tbody>{mechanism_rows or empty_mechanism_row}</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>成本与时间</h3><span>只在同一 cohort 内解释；并发 wall time 不等于 LLM latency 求和</span></div>"
        "<table><thead><tr><th>轮次</th><th>范围</th><th>Provider Token</th><th>估算成本</th><th>墙钟时间</th><th>LLM latency 求和</th></tr></thead>"
        f"<tbody>{cost_rows or empty_cost_row}</tbody></table>"
        f"<p class='boundary-note'>{_escape(cost_and_time.get('uncertainty') or '')}</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>失败 Pareto</h3><span>优化对象来自证据，不来自直觉</span></div>"
        "<table><thead><tr><th>#</th><th>失败模式</th><th>数量</th><th>证据与影响</th></tr></thead>"
        f"<tbody>{pareto_rows or empty_pareto_row}</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>逐题结果</h3><span>防止聚合指标掩盖回归</span></div>"
        f"<table><thead><tr><th>Case</th>{case_headers}<th>转移 / 说明</th></tr></thead>"
        f"<tbody>{''.join(case_rows) or empty_case_row}</tbody></table></section>",
        "<details class='evidence-section'><summary><b>Pre-R0 探索性预实验（不进入正式因果链）</b></summary>"
        f"<ol class='next-actions'>{historical_shortcoming_items}</ol>"
        "<table><thead><tr><th>旧标识</th><th>范围</th><th>留下的信号</th><th>证据边界</th></tr></thead>"
        f"<tbody>{historical_rows or empty_historical_row}</tbody></table></details>",
        "<details class='evidence-section'><summary><b>排除的无效启动</b></summary>"
        "<p class='boundary-note'>协议变量漂移的样本无论结果好坏都不进入 gate；成本只报告已确认下界。</p>"
        "<table><thead><tr><th>轮次</th><th>排除原因</th><th>观察到但不可用于结论</th><th>Provider Token 下界</th><th>成本下界</th><th>已排除</th></tr></thead>"
        f"<tbody>{excluded_rows or empty_excluded_row}</tbody></table></details>",
        "<details class='evidence-section'><summary><b>固定条件与设计复盘</b></summary>"
        f"<table><tbody>{condition_rows}</tbody></table>"
        f"<ol class='next-actions'>{''.join(f'<li>{_escape(item)}</li>' for item in story)}</ol></details>",
        rollback_html,
        f"<p class='diagnosis'>{_escape(experiment.get('conclusion') or '')}</p>",
        f"<details class='provenance'><summary>实验摘要来源</summary><code>{_escape(str(source_path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_phase2_runtime_quality_dashboard(
    phase2: dict[str, Any],
    source_path: Path | None,
) -> str:
    """展示 Phase 2 的分层证据，不把 Target、Guard 和 Golden 拼成一个分母。"""

    reference = _mapping(phase2.get("reference"))
    case_study = _mapping(phase2.get("case_study"))
    target_baseline = _mapping(case_study.get("baseline_metrics"))
    target_treatment = _mapping(case_study.get("treatment_metrics"))
    guards = _mapping(phase2.get("guards"))
    guard_metrics = _mapping(guards.get("metrics"))
    golden = _mapping(phase2.get("golden10_expansion"))
    golden_baseline = _mapping(golden.get("baseline_metrics"))
    golden_treatment = _mapping(golden.get("treatment_metrics"))
    treatment = _mapping(phase2.get("treatment"))
    usage = _mapping(phase2.get("usage"))
    target_and_guards_usage = _mapping(usage.get("target_and_guards_total"))
    golden10_usage = _mapping(usage.get("golden10_expansion"))
    phase2_total_usage = _mapping(usage.get("phase2_case_study_and_expansion_total"))
    if not phase2_total_usage and any(
        name in usage for name in ("provider_tokens", "total_tokens", "llm_calls")
    ):
        # 兼容早期 schema v3 草案的扁平 usage；正式发布摘要优先使用显式总计。
        phase2_total_usage = usage

    activation_observed = _optional_metric(
        target_treatment,
        "mechanism_activation_observed",
        "activation_observed",
    )
    if activation_observed is None:
        activation_observed = _optional_metric(
            case_study,
            "mechanism_activation_observed",
            "activation_observed",
        )
    activation_expected = _optional_metric(
        target_treatment,
        "mechanism_activation_expected",
        "activation_expected",
    )
    if activation_expected is None:
        activation_expected = _optional_metric(
            case_study,
            "mechanism_activation_expected",
            "activation_expected",
        )
    unsafe_replays = _optional_metric(
        guard_metrics,
        "unsafe_replay_count",
        "unsafe_replays",
    )
    marker = str(
        treatment.get("mechanism_marker")
        or treatment.get("marker")
        or "replay_authorized_restored_precondition"
    )
    decision = str(phase2.get("decision") or phase2.get("status") or "pending")

    net_delta_value = golden.get("net_official_resolved_delta")
    net_delta = "待运行" if net_delta_value is None else f"{int(net_delta_value):+d}"
    regressions_value = golden.get("baseline_resolved_regressions")
    if regressions_value is None:
        regression_text = "待运行"
    elif isinstance(regressions_value, list):
        regression_text = str(len(regressions_value))
    else:
        regression_text = str(regressions_value)

    gates = _mapping(phase2.get("gates"))
    gate_rows: list[str] = []
    for gate_name, gate_value in gates.items():
        gate = _mapping(gate_value)
        if gate:
            gate_status = str(
                gate.get("status")
                or gate.get("decision")
                or gate.get("result")
                or "pending"
            )
            gate_evidence = str(
                gate.get("evidence") or gate.get("detail") or gate.get("criteria") or ""
            )
        else:
            gate_status = str(gate_value or "pending")
            gate_evidence = ""
        gate_rows.append(
            "<tr>"
            f"<td>{_escape(gate_name)}</td>"
            f"<td>{_badge(gate_status, _tone_for_status(gate_status))}</td>"
            f"<td>{_escape(gate_evidence)}</td>"
            "</tr>"
        )

    golden_case_rows: list[str] = []
    for case in golden.get("case_results") or []:
        if not isinstance(case, dict):
            continue
        baseline_value = case.get("baseline")
        treatment_value = case.get("treatment")
        baseline = (
            _quality_case_result_label(baseline_value)
            if isinstance(baseline_value, dict)
            else _display_value(
                baseline_value or case.get("baseline_status") or "not_evaluated"
            )
        )
        treatment_label = (
            _quality_case_result_label(treatment_value)
            if isinstance(treatment_value, dict)
            else _display_value(
                treatment_value or case.get("treatment_status") or "not_evaluated"
            )
        )
        golden_case_rows.append(
            "<tr>"
            f"<td class='mono'>{_escape(case.get('case_id') or '')}</td>"
            f"<td>{_escape(baseline)}</td>"
            f"<td>{_escape(treatment_label)}</td>"
            f"<td>{_escape(case.get('transition') or case.get('note') or '')}</td>"
            "</tr>"
        )

    evidence_dirs_value = phase2.get("evidence_run_dirs")
    evidence_dir_items: list[str] = []
    if isinstance(evidence_dirs_value, dict):
        for group_name, values in evidence_dirs_value.items():
            grouped_values = values if isinstance(values, list) else [values]
            evidence_dir_items.extend(
                f"{group_name}: {value}" for value in grouped_values if value
            )
    elif isinstance(evidence_dirs_value, list):
        evidence_dir_items.extend(str(value) for value in evidence_dirs_value if value)

    supported_claims = _string_items(phase2.get("supported_claims"))
    unsupported_claims = _string_items(phase2.get("unsupported_claims"))
    boundaries = _string_items(phase2.get("boundaries"))
    explicit_scope = str(
        phase2.get("claim_scope")
        or "post-hoc Case-level 机制故事；不外推为 SWE-bench Verified 总体提升"
    )

    provider_tokens = _optional_metric(
        phase2_total_usage,
        "provider_tokens",
        "total_tokens",
    )
    llm_calls = _optional_metric(phase2_total_usage, "llm_calls")
    estimated_cost_value = phase2_total_usage.get("estimated_cost_usd")
    estimated_cost = (
        "待运行"
        if estimated_cost_value is None
        else f"${float(estimated_cost_value):.6f}"
    )
    usage_breakdown: list[str] = []
    for usage_label, usage_bucket in (
        ("Target+Guards", target_and_guards_usage),
        ("Golden-10", golden10_usage),
    ):
        bucket_tokens = _optional_metric(
            usage_bucket,
            "provider_tokens",
            "total_tokens",
        )
        bucket_cost_value = usage_bucket.get("estimated_cost_usd")
        if bucket_tokens is None and bucket_cost_value is None:
            continue
        bucket_tokens_text = (
            f"{bucket_tokens:,} Token" if bucket_tokens is not None else "Token 待运行"
        )
        bucket_cost_text = (
            f"${float(bucket_cost_value):.6f}"
            if bucket_cost_value is not None
            else "成本待运行"
        )
        usage_breakdown.append(
            f"{usage_label} {bucket_tokens_text} / {bucket_cost_text}"
        )
    usage_note = f"总成本 {estimated_cost}"
    if usage_breakdown:
        usage_note += "；" + "；".join(usage_breakdown)
    empty_gate_row = '<tr><td colspan="3">尚无 gate 记录。</td></tr>'
    empty_case_row = '<tr><td colspan="4">Golden-10 扩展待运行。</td></tr>'
    reference_metrics = _mapping(reference.get("metrics"))
    reference_label = str(reference.get("id") or "P2-R0")

    body = [
        "<div class='view-heading'><div><span class='view-kicker'>PHASE 2 · OPERATION LEDGER TREATMENT</span>"
        f"<h2>{_escape(phase2.get('title') or 'Runtime 恢复前置条件重放实验')}</h2></div>"
        f"{_badge(decision, _tone_for_status(decision))}</div>",
        _render_lab_brief(
            question=str(
                phase2.get("question")
                or "在恢复到已执行操作的前置状态后，一次受限重放能否保留正确候选修复？"
            ),
            input_label="分层 cohort",
            input_items=[
                f"Target: {_quality_transition(target_baseline, target_treatment)}",
                f"Guards: {_quality_result(guard_metrics)}",
                f"Golden-10: {_quality_transition(golden_baseline, golden_treatment)}",
            ],
            mechanism=(
                f"恢复前置指纹精确匹配 → 仅授权一次同运行重放 → {marker} 可观测 → 其余漂移 fail closed"
            ),
            success_criteria=str(
                phase2.get("success_criteria")
                or "Target 转正、Guards 无回归；Golden-10 仅按预注册 gate 解读。"
            ),
            boundary=explicit_scope,
        ),
        _metric_grid(
            [
                (
                    "Target 个案",
                    _quality_transition(target_baseline, target_treatment),
                    "历史独立起点 → 新鲜 Treatment",
                    _quality_result_tone(target_treatment),
                ),
                (
                    "机制激活",
                    _optional_ratio(activation_observed, activation_expected),
                    marker,
                    "ok"
                    if activation_expected is not None
                    and activation_expected > 0
                    and activation_observed == activation_expected
                    else "warn",
                ),
                (
                    "正确性 Guards",
                    _quality_result(guard_metrics),
                    f"unsafe replay {_display_value(unsafe_replays) if unsafe_replays is not None else '待运行'}",
                    _quality_result_tone(guard_metrics),
                ),
                (
                    "Golden-10 扩展",
                    _quality_transition(golden_baseline, golden_treatment),
                    f"净变化 {net_delta} · 原 resolved 回归 {regression_text}",
                    _quality_result_tone(golden_treatment),
                ),
                (
                    "Phase 2 参考",
                    f"{reference_label} · {_quality_result(reference_metrics)}",
                    str(
                        reference.get("model")
                        or reference.get("provider_model")
                        or "固定基线"
                    ),
                    "neutral",
                ),
                (
                    "Phase 2 Token / LLM",
                    (
                        f"{provider_tokens:,} / {llm_calls}"
                        if provider_tokens is not None and llm_calls is not None
                        else "待运行"
                    ),
                    usage_note,
                    "neutral",
                ),
            ]
        ),
        "<p class='boundary-note scope-warning'><strong>主张边界：</strong>"
        f"{_escape(explicit_scope)}。Target 是 post-hoc Case-level 证据；Guards 只证明固定样本无回归；未完成 Golden-10 时不宣称 population uplift。</p>",
        "<section class='evidence-section'><div class='section-title'><h3>Phase 2 · 五道验收 Gate</h3><span>先看机制和正确性，再看扩展</span></div>"
        "<table><thead><tr><th>Gate</th><th>状态</th><th>证据</th></tr></thead>"
        f"<tbody>{''.join(gate_rows) or empty_gate_row}</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Golden-10 逐题非回归</h3><span>与 Phase 1 分开显示，不混合 Target / Guard 分母</span></div>"
        "<table><thead><tr><th>Case</th><th>P2-R0</th><th>Treatment</th><th>转移 / 说明</th></tr></thead>"
        f"<tbody>{''.join(golden_case_rows) or empty_case_row}</tbody></table></section>",
        "<details class='evidence-section' open><summary><b>支持与不支持的主张</b></summary>"
        "<h4>支持</h4>"
        + _render_fact_list(supported_claims, empty_message="尚未登记额外支持主张。")
        + "<h4>不支持</h4>"
        + _render_fact_list(
            unsupported_claims,
            empty_message="不支持 SWE-bench Verified 总体解决率提升或唯一因果归因。",
        )
        + "<h4>附加边界</h4>"
        + _render_fact_list(boundaries, empty_message=explicit_scope)
        + "</details>",
        "<details class='provenance'><summary>Phase 2 当前 Trace / Usage 身份</summary>"
        + _render_fact_list(
            evidence_dir_items,
            empty_message="本机未保留 Phase 2 原始运行目录；发布摘要仍可审计。",
        )
        + f"<code>{_escape(str(source_path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence phase-two-evidence'>" + "".join(body) + "</div>"


def _render_runtime_quality_dashboard(
    experiment: dict[str, Any],
    source_path: Path | None,
) -> str:
    """schema v3 优先展示 Phase 2，同时原样保留 Phase 1 证据。"""

    phase2 = _phase2_summary(experiment)
    if not phase2:
        return _render_phase1_runtime_quality_dashboard(experiment, source_path)
    return (
        "<div class='runtime-quality-phases'>"
        + _render_phase2_runtime_quality_dashboard(phase2, source_path)
        + "<div class='boundary-note'><strong>实验分界：</strong>"
        "以下为 Phase 1 历史正式 R0-R3；模型、处理、分母和结论均不与 Phase 2 合并。</div>"
        + _render_phase1_runtime_quality_dashboard(
            experiment,
            source_path,
            retained_after_phase2=True,
        )
        + "</div>"
    )


def _quality_case_result_label(result: object) -> str:
    """把逐题机器字段压缩成可扫描的一行，不隐含官方解决。"""

    if not isinstance(result, dict):
        return "未运行"
    official = _display_value(result.get("official_status") or "not_evaluated")
    patch = "有 Patch" if result.get("patch_generated") else "无 Patch"
    semantics = str(
        result.get("patch_semantics") or result.get("candidate_semantics") or ""
    )
    semantic_labels = {
        "product_source": "产品源码 Patch",
        "product_source_plus_disposable_test": "源码 + 临时测试",
        "product_source_plus_disposable_validation": "源码 + 临时验证文件",
        "scratch_test_only": "仅临时测试",
        "empty": "无 Patch",
    }
    if semantics:
        patch = semantic_labels.get(semantics, semantics)
    stop_reason = str(result.get("stop_reason") or "")
    if stop_reason.startswith("too_many_consecutive_failed_tools"):
        stop_reason = "连续失败熔断"
    else:
        stop_reason = _display_value(stop_reason)
    transition = str(result.get("transition") or "")
    return " · ".join(
        item for item in (official, patch, stop_reason, transition) if item
    )


def _render_evaluation_dashboard(project_dir: Path) -> str:
    benchmark_run_dir = _latest_benchmark_run_dir(project_dir)
    benchmark_result_path = (
        benchmark_run_dir / "results.json" if benchmark_run_dir is not None else None
    )
    result = _latest_result_record(project_dir)
    comparison = _read_json_file(_latest_benchmark_comparison_path(project_dir))
    case_trace_path_text = str(result.get("trace_path") or "")
    case_trace = _read_json_file(
        Path(case_trace_path_text) if case_trace_path_text else None
    )
    tool_actions = [
        str(event.get("tool_call") or "")
        for event in _event_list(case_trace)
        if event.get("event_type") == "action"
    ]
    write_action_count = sum(
        tool_name in WORKSPACE_WRITE_TOOL_NAMES for tool_name in tool_actions
    )
    patch_chars = int(result.get("patch_chars") or 0)
    if patch_chars > 0:
        runtime_patch_display = f"{patch_chars} 字符"
        runtime_patch_reason = "已生成候选 Diff"
    elif write_action_count == 0 and tool_actions:
        runtime_patch_display = "0 字符（只检索，未写入）"
        runtime_patch_reason = f"{len(tool_actions)} 次工具调用均未进入写操作"
    elif write_action_count > 0:
        runtime_patch_display = "0 字符（写入未形成 Diff）"
        runtime_patch_reason = (
            f"观测到 {write_action_count} 次写意图，但工作区没有最终差异"
        )
    else:
        runtime_patch_display = "0 字符（无可用工具证据）"
        runtime_patch_reason = "当前 Trace 没有可用于解释改动来源的 action 事件"
    evaluation_status = str(result.get("evaluation_status") or "not_evaluated")
    diagnosis = _translate_evidence_text(
        result.get("diagnosis") or "没有找到诊断产物。"
    )
    diagnosis_source = str(result.get("diagnosis_source") or "legacy_or_unavailable")
    diagnosis_rule = str(result.get("diagnosis_rule_id") or "not_recorded")
    taxonomy_version = str(result.get("diagnosis_taxonomy_version") or "not_recorded")
    evidence = result.get("diagnosis_evidence") or []
    next_actions = result.get("next_actions") or []
    instance_id = str(result.get("instance_id") or "未识别 Case")
    benchmark_run_name = benchmark_run_dir.name if benchmark_run_dir else "未找到"
    scope_note = (
        f"当前结论只属于评测运行 {benchmark_run_name}。"
        "Worker、Finalizer 和协调结果属于另一条运行，请切换到“并行多 Agent”查看。"
    )
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>独立证据 · SWE-BENCH CASE</span>"
        f"<h2>{_escape(instance_id)} · 证据与结论边界</h2></div>"
        f"{_badge(evaluation_status, _tone_for_status(evaluation_status))}</div>",
        f"<p class='boundary-note scope-warning'><strong>证据作用域：</strong>{_escape(scope_note)}</p>",
        _metric_grid(
            [
                (
                    "运行结果",
                    _display_value(result.get("status") or "unknown"),
                    f"评测运行 {benchmark_run_name} 的 Agent 状态",
                    _tone_for_status(str(result.get("status") or "")),
                ),
                (
                    "失败分类",
                    _display_value(result.get("failure_class") or "unclassified"),
                    "按固定优先级分类",
                    _tone_for_status(str(result.get("failure_class") or "")),
                ),
                (
                    "诊断来源",
                    _display_value(diagnosis_source),
                    f"规则={diagnosis_rule}；分类版本={taxonomy_version}",
                    "neutral",
                ),
                (
                    "候选改动",
                    runtime_patch_display,
                    runtime_patch_reason,
                    "ok" if patch_chars else "neutral",
                ),
                (
                    "官方评测",
                    _display_value(evaluation_status),
                    "最终正确性依据",
                    _tone_for_status(evaluation_status),
                ),
                (
                    "验证角色",
                    _display_value(comparison.get("verifier_status") or "not_observed"),
                    "Runtime 内部角色",
                    _tone_for_status(str(comparison.get("verifier_status") or "")),
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>失败诊断</h3><span>为什么得到当前状态</span></div>"
        f"<p class='diagnosis'>{_escape(diagnosis)}</p>"
        f"<div class='evidence-list'>{''.join(f'<span>{_escape(item)}</span>' for item in evidence)}</div>"
        "<table><tbody>"
        f"<tr><td>诊断来源</td><td>{_escape(_display_value(diagnosis_source))}</td></tr>"
        f"<tr><td>命中规则</td><td>{_escape(diagnosis_rule)}</td></tr>"
        f"<tr><td>分类版本</td><td>{_escape(taxonomy_version)}</td></tr>"
        "<tr><td>人工复核</td><td>请查看“评测改进闭环”；自动分类结果不等于人工判断。</td></tr>"
        "</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>下一步证据</h3><span>形成更强结论前还缺什么</span></div>"
        f"<ol class='next-actions'>{''.join(f'<li>{_escape(_translate_evidence_text(item))}</li>' for item in next_actions) or '<li>没有记录下一步动作。</li>'}</ol></section>",
        "<section class='evidence-section'><div class='section-title'><h3>同任务配对对比</h3><span>任务相同，Runtime 设计不同</span></div>"
        "<table><thead><tr><th>指标</th><th>单 Agent</th><th>多 Agent</th></tr></thead><tbody>"
        f"<tr><td>状态</td><td>{_escape(_display_value(comparison.get('single_status', '-')))}</td><td>{_escape(_display_value(comparison.get('multi_status', '-')))}</td></tr>"
        f"<tr><td>模型调用</td><td>{_escape(comparison.get('single_llm_calls', '-'))}</td><td>{_escape(comparison.get('multi_llm_calls', '-'))}</td></tr>"
        f"<tr><td>工具调用</td><td>{_escape(comparison.get('single_tool_calls', '-'))}</td><td>{_escape(comparison.get('multi_tool_calls', '-'))}</td></tr>"
        f"<tr><td>估算成本</td><td>{_format_optional_cost(comparison.get('single_cost_usd'))}</td><td>{_format_optional_cost(comparison.get('multi_cost_usd'))}</td></tr>"
        f"<tr><td>是否生成候选改动</td><td>{_escape(_display_value(comparison.get('single_patch_generated', '-')))}</td><td>{_escape(_display_value(comparison.get('multi_patch_generated', '-')))}</td></tr>"
        "</tbody></table>"
        f"<p class='boundary-note'>{_escape(_translate_evidence_text(comparison.get('recommendation') or '没有记录对比建议。'))}</p></section>",
        f"<details class='provenance'><summary>当前 Case 诊断来源</summary><code>{_escape(str(benchmark_result_path or '未找到 results.json'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_benchmark_dashboard(
    project_dir: Path,
    *,
    include_canonical: bool = True,
) -> str:
    """默认展示 canonical；显式历史入口仍可读取旧 Campaign。"""

    canonical_path = (
        _canonical_showcase_summary_path(project_dir) if include_canonical else None
    )
    canonical_summary = _read_json_file(canonical_path)
    if canonical_summary.get("artifact_type") == "canonical_showcase":
        return _render_canonical_showcase_dashboard(
            canonical_summary,
            canonical_path,
        )

    campaign_dir = _latest_campaign_dir(project_dir)
    state = _latest_campaign_state(project_dir)
    summary = _latest_campaign_summary(project_dir)
    if not state or not summary:
        body = [
            "<div class='view-heading'><div><span class='view-kicker'>基础设施健康检查</span><h2>Infrastructure Smoke-5</h2></div>"
            f"{_badge('not_run', 'neutral')}</div>",
            "<p class='help strong'>从 Verified 的 500 个公开任务中人工分层选出 5 题，只用于低成本检查 dataset、checkout、tools、patch、evaluator 与 evidence wiring 是否健康。它不是随机样本、质量选择集或解决率证据。</p>",
            _metric_grid(
                [
                    ("Case 数量", "5", "五个代码仓与问题类型", "neutral"),
                    ("Runtime 配置", "2", "基础控制版 vs 治理增强版", "neutral"),
                    ("重复次数", "3", "每题每种配置各运行三次", "neutral"),
                    ("计划运行", "30", "5 × 2 × 3", "neutral"),
                    ("质量分数", "不适用", "健康检查不得进入质量 headline", "warn"),
                    ("公开证据包", "完成后生成", "默认只含脱敏证据", "neutral"),
                ]
            ),
            "<section class='evidence-section'><div class='section-title'><h3>实验契约</h3><span>运行前固定</span></div>"
            "<table><thead><tr><th>保持一致</th><th>Runtime 配置差异</th><th>最终依据</th></tr></thead><tbody>"
            "<tr><td>Case/任务输入、模型、温度、预算、安全策略、执行模式</td><td>工具可见性 + Skill 注入上下文</td><td>每题的 SWE-bench 官方评测结果</td></tr>"
            "</tbody></table><p class='boundary-note'>这里只判断基础设施链路能否完成，不用于选择模型、配置或报告解决率。</p></section>",
            "<section class='evidence-section'><div class='section-title'><h3>运行入口</h3><span>每个运行槽位都可恢复</span></div>"
            "<pre class='raw-text'>forge bench campaign --regression-set infrastructure-smoke-5 --repetitions 3 --evaluate --publish</pre>"
            "<p class='boundary-note'>付费运行前先提交源码；默认要求干净的 Git Revision，只有显式接受 --allow-dirty 时才允许脏工作区。</p></section>",
        ]
        return "<div class='evidence'>" + "".join(body) + "</div>"

    campaign_config_value = state.get("config")
    campaign_config: dict[str, Any] = (
        campaign_config_value if isinstance(campaign_config_value, dict) else {}
    )
    benchmark_config_value = campaign_config.get("benchmark")
    benchmark_config: dict[str, Any] = (
        benchmark_config_value if isinstance(benchmark_config_value, dict) else {}
    )
    variants = summary.get("variants") or {}
    paired_official = summary.get("paired_official") or {}
    paired_sample = summary.get("paired_sample") or paired_official
    status_counts = summary.get("status_counts") or {}
    source = summary.get("source") or {}
    records = [
        record for record in state.get("records") or [] if isinstance(record, dict)
    ]
    covered_cases = {str(record.get("case_id") or "") for record in records}
    covered_cases.discard("")
    covered_variants = {str(record.get("variant") or "") for record in records}
    covered_variants.discard("")
    max_repetition = max(
        (int(record.get("repetition") or 0) for record in records),
        default=0,
    )
    cohort = campaign_config.get("cohort")
    coverage_note = (
        f"当前载入批次覆盖 {len(covered_cases)} 个 Case、"
        f"{len(covered_variants)} 种配置、最高 {max_repetition} 次重复。"
    )
    if isinstance(cohort, dict):
        coverage_note += (
            f"它来自预注册集合 {cohort.get('cohort_id') or '未命名'} 的 "
            f"{cohort.get('shard') or '未命名'} 分片；每题只运行一次，"
            "可衡量当前样本覆盖，不能估计随机稳定性。"
        )
    elif len(covered_cases) < 5 or max_repetition < 3:
        coverage_note += "这是 commissioning 子集，不是完整的五题三重复实验。"
    case_input_items = []
    case_profile_rows = []
    for case_id in sorted(covered_cases):
        profile = CASE_PROFILES.get(case_id)
        if profile is None:
            case_input_items.append(case_id)
            case_profile_rows.append(
                f"<tr><td class='mono'>{_escape(case_id)}</td><td colspan='3'>没有人工维护的任务摘要。</td></tr>"
            )
            continue
        case_input_items.append(f"{case_id} · {profile.title}：{profile.summary}")
        case_profile_rows.append(
            "<tr>"
            f"<td class='mono'>{_escape(case_id)}</td>"
            f"<td>{_escape(profile.title)}</td>"
            f"<td>{_escape(profile.summary)}</td>"
            f"<td>{_render_fact_list(profile.harness_signals, empty_message='未记录')}</td>"
            "</tr>"
        )
    historical_model = str(benchmark_config.get("model") or "未记录")
    regression_set = str(campaign_config.get("regression_set") or "未记录")
    variant_rows = []
    for name, item in variants.items():
        planned = int(item.get("planned") or 0)
        official_count = int(item.get("official_evaluated") or 0)
        official_resolved = int(item.get("official_resolved") or 0)
        selected_sample_text = (
            f"{official_resolved}/{planned} ({official_resolved / planned:.1%})"
            if planned
            else "无计划样本"
        )
        evaluated_patch_text = (
            f"{official_resolved}/{official_count} ({official_resolved / official_count:.1%})"
            if official_count
            else "无官方结果"
        )
        failed_tool_calls = int(item.get("failed_tool_calls") or 0)
        tool_calls = int(item.get("tool_calls") or 0)
        failed_tool_text = (
            f"{failed_tool_calls}/{tool_calls} ({failed_tool_calls / tool_calls:.2%})"
            if tool_calls
            else "0/0"
        )
        variant_rows.append(
            "<tr>"
            f"<td><b>{_escape(_display_value(name))}</b><small class='mono'>{_escape(name)}</small></td>"
            f"<td>{int(item.get('completed') or 0)}/{int(item.get('planned') or 0)}</td>"
            f"<td>{int(item.get('patch_generated') or 0)}/{int(item.get('planned') or 0)}</td>"
            f"<td>{int(item.get('local_verified') or 0)}/{int(item.get('planned') or 0)}</td>"
            f"<td>{_escape(selected_sample_text)}</td>"
            f"<td>{_escape(evaluated_patch_text)}</td>"
            f"<td>{int(item.get('infrastructure_failures') or 0)}</td>"
            f"<td>{int(item.get('total_tokens') or 0)}</td>"
            f"<td>${float(item.get('execution_estimated_cost_usd') or item.get('estimated_cost_usd') or 0.0):.6f}</td>"
            f"<td>{_escape(failed_tool_text)}</td>"
            "</tr>"
        )
    run_rows = []
    for record in records:
        evidence = record.get("evidence") or {}
        run_rows.append(
            "<tr>"
            f"<td>{int(record.get('ordinal') or 0)}</td>"
            f"<td class='mono'>{_escape(record.get('case_id') or '')}</td>"
            f"<td>{int(record.get('repetition') or 0)}</td>"
            f"<td>{_escape(_display_value(record.get('variant') or ''))}</td>"
            f"<td>{_badge(str(record.get('status') or 'pending'), _tone_for_status(str(record.get('status') or '')))}</td>"
            f"<td>{_display_value(bool(evidence.get('patch_generated')))}</td>"
            f"<td>{_escape(_display_value(evidence.get('official_evaluation_status') or 'not_evaluated'))}</td>"
            f"<td>{_escape(_display_value(evidence.get('failure_class') or 'unclassified'))}</td>"
            "</tr>"
        )
    wins = paired_sample.get("wins") or {}
    variant_rows_html = "".join(variant_rows) or (
        "<tr><td colspan='10'>没有 Runtime 配置证据。</td></tr>"
    )
    run_rows_html = "".join(run_rows) or (
        "<tr><td colspan='8'>没有运行槽位。</td></tr>"
    )
    paired_metrics = _metric_grid(
        [
            (
                "可裁决样本配对",
                str(
                    paired_sample.get("adjudicated_pairs")
                    or paired_sample.get("evaluated_pairs")
                    or 0
                ),
                "稳定 no-patch 计未解决，基础设施故障不裁决",
                "neutral",
            ),
            (
                "基础控制版胜出",
                str(wins.get("minimal-control") or 0),
                "仅基础控制版官方解决",
                "neutral",
            ),
            (
                "治理增强版胜出",
                str(wins.get("governed-runtime") or 0),
                "仅治理增强版官方解决",
                "ok",
            ),
            (
                "结果相同",
                str(paired_sample.get("ties") or 0),
                "两种配置在该题是否解决一致",
                "neutral",
            ),
            (
                "基础设施排除",
                str(paired_sample.get("excluded_infrastructure_pairs") or 0),
                "重试一次后仍失败，不归因到 Agent",
                "warn" if paired_sample.get("excluded_infrastructure_pairs") else "ok",
            ),
            (
                "配置差异",
                "路由 + Skills",
                "多因素 Runtime 配置对比",
                "neutral",
            ),
        ]
    )
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>评测批次</span><h2>当前 Case 与重复实验结果</h2></div>"
        f"{_badge(str(summary.get('status') or 'unknown'), _tone_for_status(str(summary.get('status') or '')))}</div>",
        _render_lab_brief(
            question=(
                "同一批 SWE-bench Case 在基础控制版与治理增强版下，"
                "正确性、失败工具调用和成本分别发生了什么变化？"
            ),
            input_label="历史模型任务",
            input_items=case_input_items,
            mechanism=(
                "固定 Case 与模型 → 交替运行两套 Runtime preset → 保存 Scorecard → "
                "按 Case 配对官方结果 → 聚合效率和失败指标"
            ),
            success_criteria=(
                f"{int(status_counts.get('completed') or 0)}/{int(summary.get('planned_runs') or 0)} "
                "个运行槽位完成，"
                f"{int(paired_sample.get('adjudicated_pairs') or paired_sample.get('evaluated_pairs') or 0)} "
                "组得到可裁决的配对结果。"
            ),
            boundary=(
                f"当前页面读取 {regression_set} 的已保存证据；本次打开页面不会调用模型。"
                f"历史运行模型为 {historical_model}，样本和重复次数不足以外推总体解决率。"
            ),
        ),
        f"<p class='boundary-note'><strong>当前覆盖：</strong>{_escape(coverage_note)}</p>",
        _metric_grid(
            [
                (
                    "实验批次",
                    str(summary.get("campaign_id") or ""),
                    "稳定的实验 ID",
                    "neutral",
                ),
                (
                    "代码版本",
                    str(source.get("revision") or "unknown")[:12],
                    str(source.get("branch") or ""),
                    "neutral",
                ),
                (
                    "计划运行",
                    str(summary.get("planned_runs") or 0),
                    "Case × 配置 × 重复次数",
                    "neutral",
                ),
                (
                    "已完成",
                    str(status_counts.get("completed") or 0),
                    "持久化完成的运行槽位",
                    "ok",
                ),
                (
                    "运行失败",
                    str(status_counts.get("failed") or 0),
                    "可在恢复时重试",
                    "warn" if status_counts.get("failed") else "ok",
                ),
                (
                    "官方评测配对",
                    str(
                        paired_sample.get("adjudicated_pairs")
                        or paired_sample.get("evaluated_pairs")
                        or 0
                    ),
                    "排除重试耗尽的基础设施槽位",
                    "ok"
                    if paired_sample.get("adjudicated_pairs")
                    or paired_sample.get("evaluated_pairs")
                    else "warn",
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>Case 任务摘要</h3>"
        "<span>先知道模型当时在修什么，再查看数字</span></div>"
        "<table><thead><tr><th>Case</th><th>任务名称</th><th>问题摘要</th><th>观察能力</th></tr></thead>"
        f"<tbody>{''.join(case_profile_rows)}</tbody></table>"
        "<p class='boundary-note'>真实运行输入是 SWE-bench 的 problem_statement、仓库和 base commit；公开批次保留 Case ID 与人工摘要，不向 Agent 暴露 test patch 或 gold patch。</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>Runtime 配置对比</h3><span>核心 Runtime 相同，配置差异显式记录</span></div>"
        "<table><thead><tr><th>配置</th><th>完成</th><th>候选改动</th><th>本地验证候选</th><th>样本解决率</th><th>已评测补丁接受率</th><th>基础设施失败</th><th>Token</th><th>执行成本</th><th>失败工具调用率</th></tr></thead>"
        f"<tbody>{variant_rows_html}</tbody></table>"
        "<p class='boundary-note'>样本解决率以全部预注册 Case 为分母；已评测补丁接受率只回答“进入官方评测的补丁有多少被接受”，两者不能互换。</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>样本结果配对</h3><span>稳定 no-patch 计未解决；基础设施故障单独排除</span></div>"
        f"{paired_metrics}"
        "<p class='boundary-note'>候选改动和本地通过都不能替代 official resolved；被排除的基础设施槽位也不能算作模型失败。</p></section>",
        "<details class='drilldown'><summary>排障：查看每个 Case × 配置 × 重复次数的运行槽位</summary>"
        "<div class='drilldown-body'><div class='section-title'><h3>运行矩阵</h3><span>每个槽位都可独立恢复</span></div>"
        "<table><thead><tr><th>#</th><th>Case</th><th>第几次</th><th>配置</th><th>状态</th><th>候选改动</th><th>官方评测</th><th>失败分类</th></tr></thead>"
        f"<tbody>{run_rows_html}</tbody></table></div></details>",
        "<details class='provenance'><summary>实验批次来源</summary>"
        f"<code>{_escape(str(campaign_dir or '未找到'))}</code>"
        f"<code>配置 SHA-256：{_escape(summary.get('config_digest') or '')}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_feedback_dashboard(project_dir: Path) -> str:
    feedback_path = _latest_feedback_path(project_dir)
    feedback = _read_json_file(feedback_path)
    improvement_path = _latest_improvement_record_path(project_dir)
    improvement = _read_json_file(improvement_path)
    dataset_path = project_dir / EVALUATION_DATA_ROOT / "evidence_dataset.jsonl"
    outcome = str(feedback.get("outcome") or "unreviewed")
    decision = improvement.get("decision") or {}
    diagnosis = improvement.get("diagnosis") or {}
    before_after = improvement.get("before_after") or {}
    control = before_after.get("control") or {}
    treatment = before_after.get("treatment") or {}
    delta = before_after.get("delta") or {}
    change = improvement.get("change") or {}
    control_name = str(change.get("control_variant") or "minimal-control")
    treatment_name = str(change.get("treatment_variant") or "governed-runtime")
    campaign_summary = _latest_campaign_summary(project_dir)
    campaign_variants = campaign_summary.get("variants") or {}
    campaign_control = campaign_variants.get(control_name) or {}
    campaign_treatment = campaign_variants.get(treatment_name) or {}
    campaign_state = _latest_campaign_state(project_dir)
    campaign_config_value = campaign_state.get("config")
    campaign_config: dict[str, Any] = (
        campaign_config_value if isinstance(campaign_config_value, dict) else {}
    )
    benchmark_config_value = campaign_config.get("benchmark")
    benchmark_config: dict[str, Any] = (
        benchmark_config_value if isinstance(benchmark_config_value, dict) else {}
    )
    case_ids = _string_items(
        campaign_config.get("case_ids") or improvement.get("regression_cases")
    )
    case_input_items = []
    for case_id in case_ids:
        profile = CASE_PROFILES.get(case_id)
        if profile is None:
            case_input_items.append(case_id)
            continue
        case_input_items.append(f"{case_id} · {profile.title}：{profile.summary}")
    variant_names = [
        str(variant.get("name") or "")
        for variant in campaign_config.get("variants") or []
        if isinstance(variant, dict) and variant.get("name")
    ]
    if variant_names:
        case_input_items.append("Runtime 配置：" + " vs ".join(variant_names))
    historical_model = str(benchmark_config.get("model") or "未记录")
    repetitions = int(campaign_config.get("repetitions") or 0)
    planned_runs = int(campaign_summary.get("planned_runs") or 0)
    completed_runs = int(
        (campaign_summary.get("status_counts") or {}).get("completed") or 0
    )
    paired_official = campaign_summary.get("paired_official") or {}
    paired_sample = campaign_summary.get("paired_sample") or paired_official

    def metric_value(
        record: dict[str, Any],
        fallback: dict[str, Any],
        name: str,
    ) -> Any:
        return record[name] if name in record else fallback.get(name)

    control_failed = metric_value(control, campaign_control, "failed_tool_calls")
    treatment_failed = metric_value(treatment, campaign_treatment, "failed_tool_calls")
    control_tool_calls = metric_value(control, campaign_control, "tool_calls")
    treatment_tool_calls = metric_value(treatment, campaign_treatment, "tool_calls")
    improvement_status = str(decision.get("status") or "not_recorded")
    chain = (
        "<div class='pipeline improvement-chain'>"
        f"<div><b>01</b><span>观测问题</span><small>{_escape(_translate_evidence_text(improvement.get('observed_problem') or '未记录'))}</small></div>"
        f"<div><b>02</b><span>失败诊断</span><small>{_escape(_display_value(diagnosis.get('source') or 'not_recorded'))} · {_escape(_display_value(diagnosis.get('review_status') or 'unreviewed'))}</small></div>"
        f"<div><b>03</b><span>改进假设</span><small>{_escape(_translate_evidence_text(improvement.get('hypothesis') or '未记录'))}</small></div>"
        f"<div><b>04</b><span>实施改动</span><small>{_escape(_translate_evidence_text((improvement.get('change') or {}).get('reference') or '未记录'))}</small></div>"
        f"<div><b>05</b><span>回归验证</span><small>{len(improvement.get('regression_cases') or [])} 个配对 Case</small></div>"
        f"<div><b>06</b><span>人工决策</span><small>{_escape(_display_value(improvement_status))}</small></div>"
        "</div>"
    )
    failed_tool_call_delta = delta.get("failed_tool_calls")
    total_token_delta = delta.get("total_tokens")
    failed_tool_equation = "未记录完整对照"
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (control_failed, treatment_failed, failed_tool_call_delta)
    ):
        failed_tool_equation = (
            f"{treatment_failed:g} - {control_failed:g} = {failed_tool_call_delta:+g}"
        )
    failure_comparison_note = (
        f"{_display_value(treatment_name)} - {_display_value(control_name)}；"
        f"{_format_delta(failed_tool_call_delta, kind='failed_tools')}"
    )
    diagnosis_finding = str(diagnosis.get("finding") or "").strip()
    diagnosis_evidence = _string_items(diagnosis.get("evidence"))
    body = [
        "<div class='view-heading'><div><span class='view-kicker'>评测闭环</span><h2>评测与持续改进</h2></div>"
        f"{_badge(improvement_status, _tone_for_status(improvement_status))}</div>",
        _render_lab_brief(
            question=(
                "在 AgentLoop、模型、任务、预算和安全边界相同的条件下，"
                "面向任务的工具路由与 Skills 是否减少失败工具调用，"
                "同时不降低官方解决结果？"
            ),
            input_label="本次载入的历史实验",
            input_items=case_input_items,
            mechanism=(
                "读取 Manifest 与 Scorecard → 按 Case 配对官方结果 → "
                "聚合失败调用、Token 和成本 → 人工复核原因 → 记录继续、采纳或拒绝"
            ),
            success_criteria=(
                f"{completed_runs}/{planned_runs} 个运行槽位完成，"
                f"{int(paired_sample.get('adjudicated_pairs') or paired_sample.get('evaluated_pairs') or 0)} 组配置得到可裁决配对结果，"
                "并给出不超过证据上限的改进决策。"
            ),
            boundary=(
                f"打开评测档案不会重新调用模型；它回放由 {historical_model} 产生的已保存实验。"
                f"当前是 {len(case_ids)} 个 Case × 2 套配置 × {repetitions} 次重复；"
                "该结果只代表固定样本，不能冒充官方排行榜或总体性能。"
            ),
        ),
        "<p class='help strong'>本页所有差值都按“治理增强版 - 基础控制版”计算。官方解决数越大越好；失败工具调用和 Token 越小越好，因此这两项出现负数通常代表改善。</p>",
        _metric_grid(
            [
                (
                    "改进决策",
                    _display_value(improvement_status),
                    "采纳 / 继续迭代 / 拒绝",
                    _tone_for_status(improvement_status),
                ),
                (
                    "诊断复核",
                    _display_value(diagnosis.get("review_status") or "unreviewed"),
                    _display_value(diagnosis.get("source") or "not_recorded"),
                    "ok" if diagnosis.get("review_status") == "reviewed" else "warn",
                ),
                (
                    "官方解决数差值",
                    _format_delta(delta.get("official_resolved"), kind="official"),
                    "治理增强版 - 基础控制版",
                    "neutral",
                ),
                (
                    "失败工具调用差值",
                    failed_tool_equation,
                    failure_comparison_note,
                    "ok"
                    if isinstance(failed_tool_call_delta, (int, float))
                    and failed_tool_call_delta < 0
                    else "neutral",
                ),
                (
                    "Token 差值",
                    _format_delta(total_token_delta, kind="tokens"),
                    "正数表示治理增强版消耗更多",
                    "warn"
                    if isinstance(total_token_delta, (int, float))
                    and total_token_delta > 0
                    else "ok",
                ),
                (
                    "人工标注结果",
                    _display_value(outcome),
                    "单次运行的人工整理标签",
                    _tone_for_status(outcome),
                ),
            ]
        ),
        "<section class='evidence-section'><div class='section-title'><h3>改进决策记录</h3><span>问题 → 证据支持的决策</span></div>"
        f"{chain}"
        "<details class='drilldown' open><summary>查看失败调用的人工复核结论</summary>"
        "<div class='drilldown-body'>"
        f"<p class='boundary-note'><strong>结论：</strong> {_escape(diagnosis_finding or '当前改进记录还没有保存失败原因复核。')}</p>"
        f"{_render_fact_list(diagnosis_evidence, empty_message='当前改进记录还没有保存逐项证据。')}"
        "</div></details></section>",
        "<section class='evidence-section'><div class='section-title'><h3>改进前后证据</h3><span>直接读取同一实验批次汇总，不在页面重算指标</span></div>"
        f"<table><thead><tr><th>指标</th><th>{_escape(_display_value(control_name))}</th><th>{_escape(_display_value(treatment_name))}</th><th>差值与含义</th></tr></thead><tbody>"
        f"<tr><td>官方解决</td><td>{_escape(control.get('official_resolved', '-'))}/{_escape(control.get('official_evaluated', '-'))}</td><td>{_escape(treatment.get('official_resolved', '-'))}/{_escape(treatment.get('official_evaluated', '-'))}</td><td>{_escape(_format_delta(delta.get('official_resolved'), kind='official'))}</td></tr>"
        f"<tr><td>失败工具调用</td><td>{_escape(_format_failure_fraction(control_failed, control_tool_calls))}</td><td>{_escape(_format_failure_fraction(treatment_failed, treatment_tool_calls))}</td><td>{_escape(failed_tool_equation)}；{_escape(_format_delta(delta.get('failed_tool_calls'), kind='failed_tools'))}</td></tr>"
        f"<tr><td>Token 总量</td><td>{_escape(control.get('total_tokens', '-'))}</td><td>{_escape(treatment.get('total_tokens', '-'))}</td><td>{_escape(_format_delta(delta.get('total_tokens'), kind='tokens'))}</td></tr>"
        f"<tr><td>估算成本</td><td>${_escape(control.get('estimated_cost_usd', '-'))}</td><td>${_escape(treatment.get('estimated_cost_usd', '-'))}</td><td>{_escape(_format_delta(delta.get('estimated_cost_usd'), kind='cost'))}</td></tr>"
        "</tbody></table>"
        f"<p class='boundary-note'><strong>决策依据：</strong> {_escape(_translate_evidence_text(decision.get('rationale') or '未记录'))}</p>"
        f"<p class='boundary-note'><strong>结论边界：</strong> {_escape(_translate_evidence_text(improvement.get('claim_boundary') or '当前证据不支持更强结论。'))}</p></section>",
        "<section class='evidence-section'><div class='section-title'><h3>最近一次人工判断</h3><span>人工标签不替代 Benchmark 结论</span></div>"
        "<table><tbody>"
        f"<tr><td>结果</td><td>{_escape(_display_value(outcome))}</td></tr>"
        f"<tr><td>标签</td><td>{_escape(', '.join(str(item) for item in feedback.get('labels') or []) or '-')}</td></tr>"
        f"<tr><td>备注</td><td>{_escape(feedback.get('note') or '-')}</td></tr>"
        f"<tr><td>复核人</td><td>{_escape(feedback.get('reviewer') or '-')}</td></tr>"
        "</tbody></table></section>",
        "<section class='evidence-section'><div class='section-title'><h3>数据集导出边界</h3><span>默认保护隐私与敏感内容</span></div>"
        "<table><thead><tr><th>默认包含</th><th>默认排除</th><th>追溯来源</th></tr></thead><tbody>"
        "<tr><td>任务、停止原因、失败分类、评测状态</td><td>原始工具参数与观察结果</td><td>Trace 路径</td></tr>"
        "<tr><td>选中的上下文文件、工具序列、策略</td><td>候选改动正文</td><td>产物相对路径</td></tr>"
        "<tr><td>环境、改动大小与 SHA-256、人工反馈</td><td>模型服务密钥</td><td>Schema 版本</td></tr>"
        "</tbody></table>"
        "<p class='boundary-note'>导出的记录只是坏 Case 分析和回归集筛选的整理输入，不会自动成为生产训练数据。</p></section>",
        f"<details class='provenance'><summary>改进证据来源</summary><code>{_escape(str(improvement_path or '未找到'))}</code>"
        f"<code>{_escape(str(feedback_path or '未找到'))}</code>"
        f"<code>{_escape(str(dataset_path))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


def _render_compare_dashboard(project_dir: Path) -> str:
    run_dir = _latest_benchmark_run_dir(project_dir)
    comparison_path = _latest_benchmark_comparison_path(project_dir)
    multi_path = _latest_benchmark_multi_agent_summary_path(project_dir)
    usage_path = _latest_benchmark_usage_path(project_dir)

    comparison = _read_json_file(comparison_path)
    multi = _read_json_file(multi_path)
    usage = _read_json_file(usage_path)
    summary = usage.get("summary") or {}

    task_id = comparison.get("task_id") or multi.get("task") or "最近一次本地运行"
    single_status = str(comparison.get("single_status") or "-")
    multi_status = str(comparison.get("multi_status") or multi.get("status") or "-")
    single_patch = comparison.get("single_patch_generated", "-")
    multi_patch = comparison.get("multi_patch_generated", "-")
    single_cost = comparison.get("single_cost_usd")
    multi_cost = comparison.get("multi_cost_usd")
    cost_delta = None
    if single_cost is not None and multi_cost is not None:
        cost_delta = float(multi_cost) - float(single_cost)
    verifier_status = comparison.get("verifier_status") or "-"
    revision_rounds = comparison.get("revision_rounds", multi.get("revision_rounds", 0))
    recommendation = _translate_evidence_text(
        comparison.get("recommendation") or "请先运行一个配对对比 Case 以生成建议。"
    )
    reviewer_findings = comparison.get("reviewer_findings") or []
    reviewer_text = "<br>".join(_escape(item) for item in reviewer_findings[:3]) or "-"

    body = [
        "<h2>单 Agent 与多 Agent 对比</h2>",
        "<p class='help strong'>这个面板只回答一个问题：同一个真实缺陷，单 Agent 和多 Agent Coordinator 的工程取舍是什么。</p>",
        _metric_grid(
            [
                ("Case", str(task_id)[:90], "固定参考任务", "neutral"),
                (
                    "单 Agent",
                    _display_value(single_status),
                    "标准 AgentLoop",
                    _tone_for_status(single_status),
                ),
                (
                    "多 Agent 协调器",
                    _display_value(multi_status),
                    "实现者 / 审查者 / 验证者",
                    _tone_for_status(multi_status),
                ),
                (
                    "候选改动",
                    f"{_display_value(single_patch)} / {_display_value(multi_patch)}",
                    "单 Agent / 多 Agent 是否生成",
                    "ok" if single_patch and multi_patch else "warn",
                ),
                (
                    "验证角色",
                    _display_value(verifier_status),
                    "多 Agent 内部验证结论",
                    _tone_for_status(str(verifier_status)),
                ),
                (
                    "成本差值",
                    "-" if cost_delta is None else f"${cost_delta:.6f}",
                    "多 Agent - 单 Agent",
                    "warn" if cost_delta and cost_delta > 0 else "ok",
                ),
            ]
        ),
        "<div class='lane-grid'>",
        "<div class='lane-card'>",
        "<h3>单 Agent 路径</h3>",
        "<div class='mini-flow'><span>用户任务</span><span>AgentLoop</span><span>工具</span><span>候选改动</span></div>",
        "<p class='help'>优点是成本低、路径短、容易理解；风险是缺少独立 review/verifier 控制点。</p>",
        "<table><tbody>",
        f"<tr><td>状态</td><td>{_badge(single_status, _tone_for_status(single_status))}</td></tr>",
        f"<tr><td>是否生成候选改动</td><td>{_escape(_display_value(single_patch))}</td></tr>",
        f"<tr><td>模型调用</td><td>{_escape(comparison.get('single_llm_calls', '-'))}</td></tr>",
        f"<tr><td>工具调用</td><td>{_escape(comparison.get('single_tool_calls', '-'))}</td></tr>",
        f"<tr><td>失败工具调用</td><td>{_escape(comparison.get('single_failed_tool_calls', '-'))}</td></tr>",
        f"<tr><td>估算成本</td><td>{_format_optional_cost(single_cost)}</td></tr>",
        "</tbody></table>",
        "</div>",
        "<div class='lane-card'>",
        "<h3>多 Agent 协调路径</h3>",
        "<div class='mini-flow'><span>实现者</span><span>审查者</span><span>验证者</span><span>显式产物</span></div>",
        "<p class='help'>优点是把实现、审查、验证拆成显式控制点；代价是 token、延迟和工具调用更多。</p>",
        "<table><tbody>",
        f"<tr><td>状态</td><td>{_badge(multi_status, _tone_for_status(multi_status))}</td></tr>",
        f"<tr><td>是否生成候选改动</td><td>{_escape(_display_value(multi_patch))}</td></tr>",
        f"<tr><td>模型调用</td><td>{_escape(comparison.get('multi_llm_calls', summary.get('llm_calls', '-')))}</td></tr>",
        f"<tr><td>工具调用</td><td>{_escape(comparison.get('multi_tool_calls', summary.get('tool_calls', '-')))}</td></tr>",
        f"<tr><td>失败工具调用</td><td>{_escape(comparison.get('multi_failed_tool_calls', summary.get('failed_tool_calls', '-')))}</td></tr>",
        f"<tr><td>估算成本</td><td>{_format_optional_cost(multi_cost)}</td></tr>",
        f"<tr><td>修订轮次</td><td>{_escape(revision_rounds)}</td></tr>",
        "</tbody></table>",
        "</div>",
        "</div>",
        "<h3>工程决策</h3>",
        f"<p class='diagnosis'>{_escape(recommendation)}</p>",
        "<p class='boundary-note'>多 Agent 增加显式审查与验证控制点；这项代价是否值得，要由同任务的成本、失败与评测证据决定。</p>",
        "<h3>审查者 / 验证者结论</h3>",
        "<table><tbody>",
        f"<tr><td>验证状态</td><td>{_escape(_display_value(verifier_status))}</td></tr>",
        f"<tr><td>审查发现</td><td>{reviewer_text}</td></tr>",
        f"<tr><td>建议</td><td>{_escape(recommendation)}</td></tr>",
        "</tbody></table>",
        "<h3>生成的产物</h3>",
        _render_artifact_cards(multi),
        "<details class='provenance'><summary>产物来源</summary>"
        f"<code>{_escape(str(run_dir or '未找到'))}</code>"
        f"<code>{_escape(str(comparison_path or '未找到'))}</code>"
        f"<code>{_escape(str(multi_path or '未找到'))}</code>"
        f"<code>{_escape(str(usage_path or '未找到'))}</code></details>",
    ]
    return "<div class='evidence'>" + "".join(body) + "</div>"


_DISPLAY_VALUES = {
    "completed": "已完成",
    "completed_with_failures": "完成但存在失败",
    "running": "运行中",
    "saved": "已保存",
    "pending": "等待执行",
    "failed": "失败",
    "success": "成功",
    "succeeded": "成功",
    "passed": "通过",
    "pass": "通过",
    "needs_revision": "需要修订",
    "blocked": "已阻塞",
    "final_answer": "已生成最终回答",
    "pending_tool_call_at_stop": "停止时仍有待执行工具",
    "unknown": "未知",
    "unclassified": "未分类",
    "not_evaluated": "未进行官方评测",
    "not_observed": "本次未观测",
    "not_recorded": "未记录",
    "legacy_or_unavailable": "旧格式或不可用",
    "not_run": "未运行",
    "planned_not_run": "已规划，尚未运行",
    "available": "可用",
    "measured_reference": "已完成参考观测",
    "fixed_seen_development_sample": "固定已见开发样本",
    "unreviewed": "未人工复核",
    "reviewed": "已人工复核",
    "iterate": "继续迭代",
    "adopt": "采纳",
    "reject": "拒绝",
    "accepted": "已采纳",
    "rejected": "已拒绝",
    "baseline": "基线",
    "tool": "工具动作",
    "allow": "允许",
    "defer": "无额外意见",
    "ask": "需要人工确认",
    "deny": "拒绝",
    "planned": "已计划",
    "approved": "已批准",
    "executing": "执行中",
    "executed": "已执行",
    "failed_uncertain": "执行结果不确定",
    "fail_closed": "失败时阻断",
    "isolated": "异常隔离",
    "present": "已存在",
    "absent": "不存在",
    "observed": "已观测",
    "candidate": "候选结果",
    "local": "本地",
    "official": "官方评测",
    "derived": "派生统计",
    "presentation": "展示产物",
    "official_resolved": "官方评测已解决",
    "official_unresolved": "官方评测未解决",
    "confirmed_solved": "已确认解决",
    "confirmed_unresolved": "已确认未解决",
    "not_adjudicated": "尚未完成官方裁决",
    "cost_budget_exceeded": "达到成本预算",
    "tool_failure_circuit_breaker": "连续失败熔断",
    "official_eval_failed": "官方评测未通过",
    "validation_environment_unavailable": "验证环境不可用",
    "patch_generated_but_unverified": "已生成候选改动，但未验证",
    "no_patch_generated": "未生成候选改动",
    "tool_schema_mismatch": "工具参数不符合契约",
    "tool_execution_failure": "工具执行失败",
    "repeated_tool_failure": "工具重复失败",
    "max_steps_reached": "达到最大轮次",
    "minimal-control": "基础控制版",
    "governed-runtime": "治理增强版",
    "implementer": "实现者",
    "reviewer": "审查者",
    "verifier": "验证者",
    "coordinator": "协调器",
    "coordinator + verifier": "协调器 + 验证者",
    "run result": "运行结果",
    "next stage": "下一阶段",
    "maintainer_review": "维护者人工复核",
    "worktree": "隔离 Worktree",
    "container": "OCI 容器",
    "waiting_approval": "等待审批",
    "waiting_human_input": "等待人工输入",
    "cancelled": "已取消",
    "invalid": "无效",
    "fact": "运行事实",
    "none": "无",
}


def _display_value(value: Any) -> str:
    """把稳定的机器枚举投影为中文；未知值原样保留以便追溯。"""

    if isinstance(value, bool):
        return "是" if value else "否"
    text = str(value)
    if " · " in text:
        # 运行证据选择器会把机器状态和证据摘要拼成一行；逐段翻译，
        # 避免出现“completed · 已确认解决”这种中英文混排。
        return " · ".join(
            _DISPLAY_VALUES.get(part, _DISPLAY_VALUES.get(part.lower(), part))
            for part in text.split(" · ")
        )
    return _DISPLAY_VALUES.get(text, _DISPLAY_VALUES.get(text.lower(), text))


def _translate_runtime_summary(value: Any) -> str:
    """翻译 Runtime 生成的固定策略摘要，不改写底层证据。"""

    text = str(value)
    replacements = (
        (
            "all hooks deferred; default allow",
            "全部处理器均无额外意见，按默认规则允许",
        ),
        (
            "execution environment has no additional restriction",
            "执行环境没有附加限制",
        ),
        ("no hook opinion", "该处理器无额外意见"),
        ("explicit invocation", "显式指定"),
        ("task metadata score=", "任务元数据命中分="),
        ("bounded fallback", "有界兜底选择"),
        ("read/list/search allowed", "读取、列目录和搜索允许"),
        ("replace_text/write_file asks approval", "replace_text/write_file 需要审批"),
        ("dangerous commands denied", "危险命令拒绝"),
        ("execution_environment mode=", "执行环境模式="),
        ("active_workspace=", "活动工作区="),
        ("network_policy=", "网络策略="),
        ("writes ask", "写操作需要审批"),
        ("write needs approval", "写操作需要审批"),
        ("branch=", "分支="),
        ("dirty=True", "工作区有未提交改动=是"),
        ("dirty=False", "工作区有未提交改动=否"),
        ("执行环境模式=local", "执行环境模式=本地"),
        ("网络策略=deny", "网络策略=拒绝"),
        ("approved", "已批准"),
        (
            "final step: no more tool calls are available, provide the best evidence-based final answer and clearly mark unverified items",
            "最后一轮：不能再调用工具，请给出基于证据的最终回答，并明确标注未验证项",
        ),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def _translate_evidence_text(value: Any) -> str:
    """翻译历史证据中的固定实验术语；自由文本和技术标识保持原样。"""

    text = str(value)
    replacements = (
        (
            "The model still requested a tool on the final turn, so the runtime blocked an incomplete artifact.",
            "模型在最后一轮仍请求调用工具，因此运行时阻断了不完整产物。",
        ),
        (
            "Inspect the final model action and increase budget or force an earlier patch/no-patch decision.",
            "检查最后一次模型动作；增加步骤预算，或要求模型更早明确“生成改动/无需改动”。",
        ),
        ("commissioning evidence only", "仅限试运行证据"),
        ("correctness tied and cost increased", "正确性结果相同且成本增加"),
        ("failed tool calls were noisy", "失败工具调用噪音较多"),
        ("routing reduces failed calls", "工具路由可减少失败调用"),
        ("governed-runtime preset", "治理增强版配置"),
        ("governed preset", "治理增强版配置"),
        ("governed-runtime", "治理增强版"),
        ("Minimal-control", "基础控制版"),
        ("minimal-control", "基础控制版"),
        ("post-hoc commissioning evidence", "事后分析的试运行证据"),
        ("commissioning evidence", "试运行证据"),
        ("commissioning case", "试运行 Case"),
        ("task-aware routing", "面向任务的工具路由"),
        ("built-in Skills", "内置 Skills"),
        ("official resolved", "官方解决"),
        ("official outcome", "官方结果"),
        ("a candidate patch was produced", "已生成候选代码改动"),
        ("official benchmark resolution", "官方基准评测已解决"),
        ("candidate diff", "候选代码改动"),
        ("usage projection", "用量统计"),
        ("provider 账单", "模型服务商账单"),
        ("stop reason", "停止原因"),
        ("workspace", "工作区"),
        ("Runtime", "运行时"),
        ("trace", "运行轨迹"),
        ("tool", "工具"),
        ("checkpoint", "检查点"),
        ("hostile multi-tenant workload", "恶意多租户负载"),
        ("source artifact", "源产物"),
        (" artifact", " 产物"),
        ("trade-off", "权衡"),
        ("correctness", "正确性"),
        ("preset", "配置"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text


_CONTEXT_SECTION_LABELS = {
    "system_context": "系统上下文",
    "active_skills": "已激活 Skills",
    "tool_schemas": "工具 Schema",
    "working_memory_summary": "工作记忆摘要",
    "conversation_history": "会话历史",
    "permission_summary": "权限摘要",
    "system": "系统指令",
    "attention_sink": "注意力保留区",
    "available_tools": "可用工具",
    "project_instructions": "项目指令",
    "context_state": "上下文状态",
    "repo_map": "代码仓地图",
    "long_term_memory": "长期记忆",
}


def _display_context_section(value: Any) -> str:
    text = str(value)
    return _CONTEXT_SECTION_LABELS.get(text, text)


def _format_delta(value: Any, *, kind: str) -> str:
    """把实验差值连同方向含义一起展示，避免读者猜正负号。"""

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "未记录"
    numeric = float(value)
    if kind == "cost":
        signed = f"{numeric:+.6f}"
        if numeric == 0:
            return "$0.000000（没有变化）"
        direction = "多花" if numeric > 0 else "少花"
        return f"{signed[0]}${signed[1:]}（{direction} ${abs(numeric):.6f}）"

    amount = int(numeric) if numeric.is_integer() else numeric
    signed = f"{amount:+,}"
    absolute = f"{abs(amount):,}"
    if numeric == 0:
        return "0（没有变化）"
    if kind == "official":
        meaning = f"{'多' if numeric > 0 else '少'}解决 {absolute} 个"
    elif kind == "failed_tools":
        meaning = f"{'多' if numeric > 0 else '少'} {absolute} 次失败"
    elif kind == "tokens":
        meaning = f"{'多用' if numeric > 0 else '少用'} {absolute} Token"
    else:
        meaning = (
            "治理增强版高于基础控制版" if numeric > 0 else "治理增强版低于基础控制版"
        )
    return f"{signed}（{meaning}）"


def _format_failure_fraction(failed: Any, total: Any) -> str:
    """同时展示失败次数、调用总数和比例，避免孤立数字失去分母。"""

    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (failed, total)
    ):
        return "未记录"
    if float(total) <= 0:
        return f"{failed:g} / {total:g}（没有可计算的调用）"
    rate = float(failed) / float(total) * 100
    return f"{failed:g} / {total:g}（{rate:.1f}%）"


def _format_optional_cost(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"${float(value):.6f}"
    except (TypeError, ValueError):
        return _escape(value)


def _render_role_rows(summary: dict[str, Any]) -> str:
    rows = []
    for result in summary.get("role_results") or []:
        final_answer = str(result.get("final_answer") or result.get("output") or "")
        rows.append(
            "<tr>"
            f"<td>{_escape(_display_value(result.get('role') or result.get('name') or '-'))}</td>"
            f"<td>{_badge(str(result.get('decision') or result.get('status') or '-'), _tone_for_status(str(result.get('decision') or result.get('status') or '')))}</td>"
            f"<td>{_escape(result.get('steps', '-'))}</td>"
            f"<td class='mono'>{_escape(result.get('artifact_path') or result.get('artifact') or '-')}</td>"
            f"<td>{_escape(final_answer[:220])}</td>"
            "</tr>"
        )
    if rows:
        return "".join(rows)
    return "<tr><td colspan='5'>尚未找到多 Agent 角色摘要。请运行多 Agent/配对模式，或把此页作为离线证据导航入口。</td></tr>"


def _render_artifact_rows(summary: dict[str, Any]) -> str:
    artifacts = summary.get("artifacts") or summary.get("artifact_index") or []
    if isinstance(artifacts, dict):
        artifacts = artifacts.get("artifacts") or list(artifacts.values())
    rows = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{_escape(artifact.get('name') or artifact.get('kind') or artifact.get('artifact_id') or '-')}</td>"
            f"<td>{_escape(_display_value(artifact.get('producer') or artifact.get('role') or '-'))}</td>"
            f"<td class='mono'>{_escape(artifact.get('path') or artifact.get('relative_path') or '-')}</td>"
            "<td>后续角色只读取显式 artifact，避免把中间思考和无关上下文全部塞进 prompt。</td>"
            "</tr>"
        )
    if rows:
        return "".join(rows)
    return (
        "<tr><td>implementer_output</td><td>实现者</td><td class='mono'>multi_agent/artifacts/*.md</td>"
        "<td>候选 patch / 方案交给 reviewer。</td></tr>"
        "<tr><td>review_findings</td><td>审查者</td><td class='mono'>multi_agent/artifacts/*.md</td>"
        "<td>明确 PASS / NEEDS_REVISION / BLOCKED。</td></tr>"
        "<tr><td>verification_result</td><td>验证者</td><td class='mono'>multi_agent/artifacts/*.md</td>"
        "<td>验证结果触发修订或结束。</td></tr>"
    )


def _metric_grid(items: list[tuple[str, str, str, str]]) -> str:
    cards = []
    for label, value, note, tone in items:
        cards.append(
            f"<div class='metric-card {tone}'>"
            f"<div class='metric-label'>{_escape(label)}</div>"
            f"<div class='metric-value'>{_escape(value)}</div>"
            f"<div class='metric-note'>{_escape(note)}</div>"
            "</div>"
        )
    return "<div class='metric-grid'>" + "".join(cards) + "</div>"


def _badge(text: str, tone: str) -> str:
    return f"<span class='badge {tone}'>{_escape(_display_value(text))}</span>"


def _tone_for_status(value: str) -> str:
    normalized_status = str(value).lower()
    failure_markers = (
        "blocked",
        "failed",
        "error",
        "repeated",
        "deny",
        "pending_tool_call_at_stop",
    )
    terminal_status = normalized_status.rsplit(" · ", maxsplit=1)[-1]
    accepted_decision = terminal_status in {"accepted", "adopted"} and not any(
        marker in normalized_status for marker in failure_markers
    )
    if (
        "patch_generated" in normalized_status
        or accepted_decision
        or normalized_status
        in {
            "allow",
            "accepted",
            "adopted",
            "approved",
            "executed",
            "ok",
            "pass",
            "passed",
            "succeeded",
            "success",
        }
    ):
        return "ok"
    if any(marker in normalized_status for marker in failure_markers):
        return "bad"
    if normalized_status in {"ask", "executing", "planned", "waiting_approval"}:
        return "warn"
    if any(
        marker in normalized_status
        for marker in ("no_patch", "not_evaluated", "unavailable", "missing")
    ):
        return "warn"
    return "neutral"


def _trace_scope_label(trace_path: Path | None) -> str:
    if not trace_path:
        return "未知 Trace"
    parts = set(trace_path.parts)
    text = str(trace_path)
    if "verify" in parts:
        return "验证冒烟 Trace"
    if "multi" in parts or "__multi" in text:
        return "多 Agent Trace"
    if "single" in parts or "__single" in text:
        return "单 Agent Trace"
    return "Agent 运行 Trace"


def _empty_evidence(message: str) -> str:
    return f"<div class='evidence'><h2>尚无证据</h2><p>{_escape(message)}</p></div>"


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NanoHarness 证据工作台</title>
  <link rel="icon" href="data:," />
  <style>
    :root {
      --bg: #f5f5f7;
      --surface: rgba(255, 255, 255, .74);
      --panel: rgba(255, 255, 255, .84);
      --panel-2: rgba(242, 244, 248, .94);
      --panel-3: rgba(250, 250, 252, .92);
      --text: #1d1d1f;
      --muted: #6e7582;
      --line: rgba(60, 60, 67, .16);
      --accent: #0a84ff;
      --accent-strong: #0066cc;
      --blue: #0a84ff;
      --purple: #8e8cf0;
      --yellow: #b7791f;
      --red: #d70015;
      --shadow: rgba(0, 0, 0, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
      background: #f4f6f8;
      color: var(--text);
    }
    header {
      padding: 18px 28px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      background: rgba(245, 245, 247, .82);
      backdrop-filter: blur(22px) saturate(1.35);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1 { margin: 0; font-size: 25px; font-weight: 760; letter-spacing: 0; }
    .eyebrow {
      color: var(--accent-strong);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .08em;
      margin-bottom: 6px;
    }
    .subtitle { margin-top: 4px; color: var(--muted); font-size: 14px; }
    .project-chip {
      max-width: 420px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: rgba(255, 255, 255, .72);
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .header-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      min-width: 360px;
    }
    .header-actions button {
      width: auto;
      margin: 0;
      white-space: nowrap;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      min-height: calc(100vh - 79px);
    }
    body.status-collapsed .status { display: none; }
    body.focus-mode header {
      padding: 10px 18px;
    }
    body.focus-mode .eyebrow,
    body.focus-mode .subtitle,
    body.focus-mode .project-chip,
    body.focus-mode .status {
      display: none;
    }
    body.focus-mode main {
      min-height: calc(100vh - 49px);
    }
    body.focus-mode section {
      padding: 16px 22px 22px;
    }
    body.focus-mode .output {
      max-height: none;
      min-height: calc(100vh - 134px);
    }
    section {
      padding: 18px 28px 28px;
      max-width: 1720px;
      width: 100%;
      margin: 0 auto;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
      box-shadow: 0 12px 34px var(--shadow);
    }
    .card h2 {
      font-size: 16px;
      margin: 0 0 10px;
    }
    .section-kicker {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .06em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .route-map {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      margin: 10px 0 4px;
    }
    .route-step {
      display: grid;
      grid-template-columns: 34px 1fr;
      gap: 10px;
      align-items: start;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel-3);
    }
    .route-num {
      width: 26px;
      height: 26px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      font-weight: 900;
      color: white;
      background: var(--accent);
    }
    .route-title { font-weight: 800; }
    .route-copy { color: var(--muted); font-size: 12px; line-height: 1.5; margin-top: 2px; }
    .help {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
      margin: 6px 0 10px;
    }
    .command {
      display: block;
      margin-top: 6px;
      color: #42556b;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      overflow-wrap: anywhere;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin: 10px 0 6px;
    }
    input, select, textarea {
      width: 100%;
      background: rgba(255, 255, 255, .9);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
    }
    textarea { min-height: 76px; resize: vertical; }
    button {
      width: 100%;
      border: 0;
      border-radius: 6px;
      background: var(--blue);
      color: white;
      font-weight: 700;
      padding: 9px 12px;
      margin-top: 10px;
      cursor: pointer;
      transition: transform .12s ease, box-shadow .12s ease, background .12s ease, filter .12s ease;
    }
    button:hover { filter: brightness(1.03); transform: translateY(-1px); }
    button:active { transform: translateY(0); filter: brightness(.98); }
    button.secondary { background: var(--panel-2); color: var(--text); border: 1px solid var(--line); }
    button.warn { background: #fff4d8; color: #7a4d00; }
    button.primary { background: var(--accent); color: white; }
    button.ghost { background: transparent; color: var(--text); border: 1px solid var(--line); }
    .action-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .action-grid button { margin-top: 0; }
    details {
      border-top: 1px solid var(--line);
      margin-top: 12px;
      padding-top: 10px;
    }
    summary {
      color: var(--muted);
      cursor: pointer;
      font-size: 12px;
    }
    .status {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .pill {
      padding: 12px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: var(--panel);
    }
    .pill .k { color: var(--muted); font-size: 12px; }
    .pill .v { margin-top: 4px; font-size: 14px; overflow-wrap: anywhere; }
    .tabs { display: flex; gap: 8px; margin-bottom: 12px; }
    .tabs button { width: auto; margin: 0; padding: 8px 10px; }
    .view-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
      align-items: center;
      position: sticky;
      top: 81px;
      z-index: 8;
      padding: 10px 0;
      background: #f4f6f8;
    }
    .view-tabs button {
      width: auto;
      margin: 0;
      padding: 8px 12px;
      font-size: 13px;
      color: var(--text);
      background: rgba(255, 255, 255, .78);
      border: 1px solid var(--line);
      box-shadow: 0 2px 8px rgba(0, 0, 0, .04);
    }
    .view-tabs button.active {
      color: #0057b8;
      background: #e8f3ff;
      border-color: rgba(10, 132, 255, .34);
      box-shadow: 0 3px 10px rgba(10, 132, 255, .12);
    }
    .evidence-menu {
      position: relative;
      flex: 0 0 auto;
      margin: 0;
      padding: 0;
      border: 0;
    }
    .evidence-menu[hidden], .evidence-menu .menu-group[hidden] { display: none; }
    .evidence-menu > summary {
      min-height: 42px;
      display: flex;
      align-items: center;
      padding: 0 13px;
      color: #5a6573;
      font-weight: 650;
      list-style: none;
    }
    .evidence-menu > summary::-webkit-details-marker { display: none; }
    .evidence-menu > summary::after { content: " ▾"; }
    .evidence-menu[open] > summary::after { content: " ▴"; }
    .evidence-menu > div {
      position: absolute;
      z-index: 20;
      top: 42px;
      left: 0;
      min-width: 270px;
      padding: 6px;
      background: #fff;
      border: 1px solid var(--line);
      box-shadow: 0 12px 30px rgba(24, 31, 39, .14);
    }
    .evidence-menu .menu-label {
      display: block;
      padding: 9px 9px 4px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .evidence-menu .menu-group + .menu-group {
      margin-top: 5px;
      padding-top: 5px;
      border-top: 1px solid var(--line);
    }
    .evidence-menu > div button {
      display: block;
      width: 100%;
      min-height: 34px;
      padding: 7px 9px;
      text-align: left;
      border: 0;
    }
    .output {
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(255, 255, 255, .88);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 24px 28px;
      min-height: calc(100vh - 245px);
      max-height: none;
      overflow: auto;
      color: var(--text);
      box-shadow: 0 18px 46px rgba(0, 0, 0, .07);
    }
    .evidence { white-space: normal; color: var(--text); }
    .evidence h2 { margin: 0 0 8px; font-size: 20px; }
    .evidence h3 { margin: 18px 0 8px; font-size: 15px; }
    .strong { color: #2f3338; }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0;
    }
    .metric-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: rgba(250, 250, 252, .88);
    }
    .metric-card.ok { border-color: rgba(52, 199, 89, .34); background: rgba(245, 252, 247, .92); }
    .metric-card.warn { border-color: rgba(183, 121, 31, .3); background: rgba(255, 250, 240, .92); }
    .metric-card.bad { border-color: rgba(215, 0, 21, .28); background: rgba(255, 247, 248, .92); }
    .metric-label, .label { color: var(--muted); font-size: 12px; margin-right: 8px; }
    .metric-value { margin-top: 4px; font-size: 18px; font-weight: 800; overflow-wrap: anywhere; }
    .metric-note { margin-top: 4px; color: var(--muted); font-size: 12px; }
    .lab-brief {
      margin: 14px 0;
      border: 1px solid var(--line);
      background: #fff;
    }
    .lab-question {
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr);
      gap: 14px;
      padding: 15px 16px;
      border-left: 4px solid var(--accent);
      border-bottom: 1px solid var(--line);
    }
    .lab-question span { color: var(--muted); font-size: 11px; font-weight: 800; }
    .lab-question strong { font-size: 14px; line-height: 1.55; }
    .lab-brief-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .lab-brief-grid > div {
      min-width: 0;
      padding: 14px 16px;
      border-right: 1px solid var(--line);
    }
    .lab-brief-grid > div:last-child { border-right: 0; }
    .lab-brief-grid b { display: block; margin-bottom: 7px; font-size: 11px; }
    .lab-brief-grid p, .lab-brief-grid .fact-list {
      margin: 0;
      color: #384454;
      font-size: 11px;
      line-height: 1.55;
    }
    .task-summary {
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      margin: 12px 0;
      padding: 12px 14px;
      border-left: 3px solid var(--accent);
      background: rgba(255, 255, 255, .62);
      line-height: 1.55;
    }
    .task-summary > span { color: var(--muted); font-size: 12px; font-weight: 800; }
    .scope-hierarchy {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      border: 1px solid var(--line);
      background: #fff;
    }
    .scope-hierarchy > div {
      min-width: 0;
      padding: 14px;
      border-right: 1px solid var(--line);
    }
    .scope-hierarchy > div:last-child { border-right: 0; }
    .scope-hierarchy b {
      display: block;
      color: #9aa3af;
      font: 700 10px/1 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .scope-hierarchy span, .scope-hierarchy strong, .scope-hierarchy small { display: block; }
    .scope-hierarchy span { margin-top: 8px; color: var(--muted); font-size: 11px; }
    .scope-hierarchy strong { margin-top: 4px; font-size: 15px; overflow-wrap: anywhere; }
    .scope-hierarchy small { margin-top: 5px; color: var(--muted); font-size: 10px; }
    .scenario-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .scenario-grid button {
      display: grid;
      grid-template-columns: 28px 1fr;
      gap: 4px 10px;
      align-items: center;
      margin: 0;
      padding: 14px;
      color: var(--text);
      text-align: left;
      background: #fff;
      border: 1px solid var(--line);
    }
    .scenario-grid button:hover { border-color: rgba(10, 132, 255, .42); background: #f8fbff; }
    .scenario-grid button b {
      grid-row: 1 / span 2;
      width: 26px;
      height: 26px;
      display: grid;
      place-items: center;
      color: #fff;
      background: var(--accent);
      border-radius: 50%;
    }
    .scenario-grid button strong { font-size: 14px; }
    .scenario-grid button span { color: var(--muted); font-size: 11px; line-height: 1.45; }
    .answer-strip {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      border: 1px solid var(--line);
      background: #fff;
    }
    .answer-strip > div { padding: 13px 14px; border-right: 1px solid var(--line); }
    .answer-strip > div:last-child { border-right: 0; }
    .answer-strip b, .answer-strip span { display: block; }
    .answer-strip b { font-size: 12px; }
    .answer-strip span { margin-top: 5px; color: var(--muted); font-size: 11px; line-height: 1.45; }
    .story-stage-list { border: 1px solid var(--line); background: #fff; }
    .story-stage { padding: 14px; border-bottom: 1px solid var(--line); }
    .story-stage:last-child { border-bottom: 0; }
    .story-stage-head {
      display: grid;
      grid-template-columns: 30px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
    }
    .story-stage-head > b {
      color: #9aa3af;
      font: 700 11px/1.4 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .story-stage h4 { margin: 0; font-size: 13px; }
    .story-stage p { margin: 4px 0 0; color: #384454; font-size: 12px; line-height: 1.45; }
    .story-stage > span { display: block; margin: 8px 0 0 40px; color: var(--muted); font-size: 10px; }
    .story-stage details { margin-left: 40px; border: 0; padding: 0; }
    .story-stage-detail { display: grid; gap: 5px; padding-top: 8px; color: var(--muted); font-size: 11px; }
    .drilldown {
      margin: 12px 0;
      padding: 0;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, .62);
    }
    .drilldown > summary {
      padding: 12px 14px;
      color: #405266;
      font-weight: 750;
      list-style-position: inside;
    }
    .drilldown[open] > summary { border-bottom: 1px solid var(--line); }
    .drilldown-body { padding: 12px 14px; }
    .evidence-artifact-grid, .worker-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 12px;
    }
    .evidence-artifact, .worker-card {
      min-width: 0;
      border: 1px solid var(--line);
      padding: 13px;
      background: #fff;
    }
    .evidence-artifact p, .worker-card p { margin: 8px 0; font-size: 12px; line-height: 1.5; }
    .evidence-artifact .boundary { color: #8a4f00; }
    .evidence-artifact details span, .worker-card details code {
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 10px;
      overflow-wrap: anywhere;
    }
    .worker-facts { display: flex; gap: 10px; color: var(--muted); font-size: 10px; }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      overflow-wrap: anywhere;
    }
    .badge {
      display: inline-block;
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
      border: 1px solid var(--line);
      color: var(--text);
      background: rgba(242, 244, 248, .84);
    }
    td .badge {
      max-width: 100%;
      border-radius: 4px;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .badge.ok, .event-pill.ok { border-color: rgba(52, 199, 89, .34); color: #248a3d; background: rgba(52, 199, 89, .09); }
    .badge.warn, .event-pill.warn { border-color: rgba(255, 149, 0, .34); color: #a35f00; background: rgba(255, 149, 0, .1); }
    .badge.bad, .event-pill.bad { border-color: rgba(215, 0, 21, .28); color: var(--red); background: rgba(215, 0, 21, .08); }
    .event-pill.blue { border-color: rgba(10, 132, 255, .32); color: #0057b8; background: rgba(10, 132, 255, .09); }
    .event-pill.purple { border-color: rgba(142, 140, 240, .34); color: #5e5ce6; background: rgba(142, 140, 240, .1); }
    .event-pill.neutral { color: var(--muted); }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 8px 0 12px;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 9px 8px;
      text-align: left;
      vertical-align: top;
    }
    tr:hover td { background: rgba(0, 0, 0, .018); }
    th { color: var(--muted); font-weight: 700; white-space: nowrap; word-break: normal; }
    td { overflow-wrap: anywhere; }
    .fact-list {
      display: grid;
      gap: 5px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .fact-list li {
      position: relative;
      padding-left: 12px;
      line-height: 1.45;
    }
    .fact-list li::before {
      content: "";
      position: absolute;
      top: .58em;
      left: 0;
      width: 4px;
      height: 4px;
      border-radius: 1px;
      background: #7a8796;
    }
    .table-note { color: var(--muted); font-size: 11px; line-height: 1.45; }
    .split {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .talking-list {
      margin: 8px 0 12px;
      padding-left: 22px;
      color: #2f3338;
      line-height: 1.65;
    }
    .talking-list li { margin: 5px 0; }
    .flow-strip {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
      margin: 10px 0 12px;
    }
    .flow-strip span {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(255, 255, 255, .72);
      padding: 10px 8px;
      text-align: center;
      color: #405266;
      font-size: 12px;
      font-weight: 700;
    }
    .lane-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin: 12px 0;
    }
    .lane-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      background: rgba(250, 250, 252, .82);
    }
    .mini-flow {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      margin: 10px 0;
    }
    .mini-flow span {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 6px;
      text-align: center;
      color: #2f3338;
      font-size: 12px;
      background: rgba(255, 255, 255, .72);
    }
    .timeline-mental-model span { text-align: left; }
    .timeline-mental-model b, .timeline-mental-model small { display: block; }
    .timeline-mental-model small {
      margin-top: 5px;
      color: var(--muted);
      font-size: 10px;
      line-height: 1.4;
    }
    .timeline-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }
    .timeline-head > div { min-width: 0; }
    .timeline-head strong, .timeline-head small { display: block; }
    .timeline-head small { margin-top: 3px; color: var(--muted); font-size: 10px; font-weight: 500; }
    .timeline-run-level {
      margin: 4px 0 10px;
      padding: 12px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }
    .timeline-turn {
      padding: 16px 0 14px;
      border-bottom: 1px solid var(--line);
    }
    .timeline-phase-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      border: 1px solid var(--line);
      background: #fff;
    }
    .timeline-phase {
      min-width: 0;
      min-height: 108px;
      padding: 12px;
      border-right: 1px solid var(--line);
      border-top: 3px solid transparent;
    }
    .timeline-phase:last-child { border-right: 0; }
    .timeline-phase.purple { border-top-color: rgba(94, 92, 230, .72); }
    .timeline-phase.blue { border-top-color: rgba(0, 87, 184, .72); }
    .timeline-phase.warn { border-top-color: rgba(163, 95, 0, .72); }
    .timeline-phase.ok { border-top-color: rgba(36, 138, 61, .72); }
    .timeline-phase.neutral { border-top-color: rgba(102, 112, 125, .46); }
    .timeline-phase.empty { background: #f7f8fa; border-top-color: #d8dde4; }
    .timeline-phase-head {
      display: flex;
      align-items: baseline;
      gap: 7px;
      margin-bottom: 10px;
    }
    .timeline-phase-head b {
      color: #9aa3af;
      font: 700 10px/1 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .timeline-phase-head span { color: #384454; font-size: 11px; font-weight: 800; }
    .timeline-phase > strong {
      display: block;
      min-height: 34px;
      color: #202938;
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .timeline-phase.empty > strong { color: #9aa3af; font-weight: 600; }
    .timeline-phase > small {
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-size: 9px;
      line-height: 1.4;
    }
    .timeline-raw {
      margin-top: 9px;
      padding: 0;
      border: 0;
      background: transparent;
    }
    .timeline-stage-drilldowns {
      display: grid;
      gap: 5px;
      padding: 9px 10px 2px;
      border: 1px solid var(--line);
      border-top: 0;
      background: #fbfcfd;
    }
    .timeline-raw summary {
      color: #66707d;
      font-size: 10px;
      font-weight: 700;
      cursor: pointer;
    }
    .timeline-raw-events { padding-top: 9px; }
    .timeline-raw-events th:nth-child(1),
    .timeline-raw-events td:nth-child(1) { width: 4%; }
    .timeline-raw-events th:nth-child(2),
    .timeline-raw-events td:nth-child(2) { width: 14%; }
    .timeline-raw-events th:nth-child(3),
    .timeline-raw-events td:nth-child(3) { width: 13%; }
    .timeline-raw-events th:nth-child(4),
    .timeline-raw-events td:nth-child(4) { width: 21%; }
    .timeline-raw-events th:nth-child(5),
    .timeline-raw-events td:nth-child(5) { width: 30%; }
    .timeline-raw-events th:nth-child(6),
    .timeline-raw-events td:nth-child(6) { width: 10%; }
    .timeline-raw-events th:nth-child(7),
    .timeline-raw-events td:nth-child(7) { width: 8%; }
    .event-pill {
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 6px 10px;
      margin: 0 7px 7px 0;
      font-size: 12px;
      background: rgba(255, 255, 255, .76);
    }
    .legend-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 12px 0;
    }
    .legend-item {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      background: rgba(255, 255, 255, .76);
      color: var(--muted);
    }
    .legend-item.blue { border-color: rgba(10, 132, 255, .32); color: #0057b8; background: rgba(10, 132, 255, .09); }
    .legend-item.purple { border-color: rgba(142, 140, 240, .34); color: #5e5ce6; background: rgba(142, 140, 240, .1); }
    .legend-item.warn { border-color: rgba(255, 149, 0, .34); color: #a35f00; background: rgba(255, 149, 0, .1); }
    .legend-item.ok { border-color: rgba(52, 199, 89, .34); color: #248a3d; background: rgba(52, 199, 89, .09); }
    .legend-item.bad { border-color: rgba(215, 0, 21, .28); color: var(--red); background: rgba(215, 0, 21, .08); }
    .legend-item.neutral {
      color: var(--muted);
    }
    .raw-text {
      white-space: pre-wrap;
      color: #2f3b49;
      margin: 0;
      font: inherit;
    }
    .job {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 10px;
      cursor: pointer;
    }
    .job strong { display: block; }
    .job span { color: var(--muted); font-size: 12px; }
    .succeeded { color: var(--accent-strong); }
    .failed { color: var(--red); }
    .running { color: var(--yellow); }
    @media (max-width: 900px) {
      header {
        position: static;
        align-items: stretch;
        flex-direction: column;
        padding: 14px 16px;
        gap: 12px;
      }
      h1 { font-size: 24px; }
      .header-actions {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        min-width: 0;
        gap: 8px;
      }
      .header-actions button {
        width: 100%;
        min-width: 0;
        white-space: normal;
        padding: 8px 6px;
      }
      .project-chip {
        grid-column: 1 / -1;
        max-width: none;
        padding: 8px 10px;
      }
      main { grid-template-columns: 1fr; }
      section { padding: 10px 12px 18px; }
      .view-tabs {
        top: 0;
        flex-wrap: nowrap;
        overflow-x: auto;
        overscroll-behavior-x: contain;
        padding: 8px 0;
        scrollbar-width: thin;
      }
      .view-tabs button { flex: 0 0 auto; }
      .output { padding: 18px 16px; min-height: calc(100vh - 250px); }
      .status { grid-template-columns: 1fr; }
      .metric-grid, .split, .flow-strip, .lane-grid, .mini-flow, .action-grid { grid-template-columns: 1fr; }
    }

    /* Evidence console v2: dense operational surface, not a marketing page. */
    :root {
      --bg: #eef1f4;
      --surface: #ffffff;
      --panel: #ffffff;
      --panel-2: #f6f7f9;
      --panel-3: #fafbfc;
      --text: #17191d;
      --muted: #66707d;
      --line: #d9dee5;
      --accent: #1769e0;
      --accent-strong: #1157bb;
      --blue: #1769e0;
      --purple: #6d4bd1;
      --yellow: #9a6200;
      --red: #b4232f;
      --green: #16794a;
    }
    body { background: var(--bg); }
    header {
      min-height: 64px;
      padding: 0 20px;
      background: #15181d;
      color: #fff;
      border: 0;
      backdrop-filter: none;
      box-shadow: none;
    }
    .brand-lockup { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .brand-mark {
      display: grid; place-items: center; width: 34px; height: 34px;
      border: 1px solid #4d5662; border-radius: 6px; color: #9ec2ff;
      font-weight: 800; font-size: 13px;
    }
    header h1 { font-size: 17px; font-weight: 700; }
    header .subtitle { color: #9fa8b4; font-size: 12px; margin: 2px 0 0; }
    header .eyebrow { display: none; }
    .header-actions { min-width: 0; gap: 6px; }
    .header-actions button {
      width: auto; padding: 7px 10px; background: transparent; color: #dbe2eb;
      border-color: #424a55; font-size: 12px; box-shadow: none;
    }
    .header-actions button:hover { background: #252a31; }
    .project-chip {
      max-width: 290px; padding: 7px 9px; border-color: #424a55;
      background: #20242a; color: #aeb8c4; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis;
    }
    main { grid-template-columns: minmax(0, 1fr); max-width: none; min-height: calc(100vh - 64px); }
    section { padding: 0 24px 32px; min-width: 0; }
    .section-kicker, .view-kicker {
      display: block; color: #687587; font-size: 10px; font-weight: 800;
      letter-spacing: 0; text-transform: uppercase; margin-bottom: 4px;
    }
    label { margin: 10px 0 5px; color: #4d5867; font-size: 11px; font-weight: 700; }
    input, select, textarea {
      min-height: 34px; padding: 7px 9px; border: 1px solid #cfd5dd;
      border-radius: 5px; background: #fff; font-size: 12px; color: var(--text);
      box-shadow: none;
    }
    textarea { min-height: 92px; resize: vertical; }
    button { border-radius: 5px; box-shadow: none; font-size: 12px; min-height: 34px; }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.secondary { background: #fff; color: #303845; border-color: #cfd5dd; }
    details { border-radius: 5px; }
    details summary { cursor: pointer; font-size: 12px; font-weight: 700; }
    .status {
      position: sticky; top: 64px; z-index: 8; display: grid;
      grid-template-columns: 110px minmax(220px, 1fr) 160px;
      gap: 0; margin: 0 -24px; padding: 0 24px; background: #fff;
      border-bottom: 1px solid var(--line);
    }
    .status .pill { border: 0; border-right: 1px solid var(--line); border-radius: 0; padding: 10px 14px; background: transparent; }
    .status .pill:first-child { border-left: 1px solid var(--line); }
    .pill .k { color: #768191; font-size: 9px; text-transform: uppercase; font-weight: 800; }
    .pill .v { margin-top: 2px; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .view-tabs {
      position: sticky; top: 110px; z-index: 7; display: flex; flex-wrap: wrap;
      gap: 0; margin: 0 -24px 20px; padding: 0 24px; overflow: visible;
      background: #fff; border-bottom: 1px solid var(--line); backdrop-filter: none;
    }
    body.status-collapsed .view-tabs { top: 64px; }
    .view-tabs button {
      flex: 0 0 auto; width: auto; min-height: 42px; margin: 0; padding: 0 13px;
      color: #5a6573; background: transparent; border: 0; border-bottom: 2px solid transparent;
      border-radius: 0; font-weight: 650;
    }
    .view-tabs button.active { color: var(--accent-strong); background: transparent; border-bottom-color: var(--accent); }
    .view-tabs .utility { margin-left: auto; color: #6c7785; }
    .output { min-height: 620px; padding: 0; background: transparent; border: 0; border-radius: 0; box-shadow: none; }
    .evidence { max-width: 1320px; margin: 0 auto; }
    .evidence h2 { margin: 0; font-size: 22px; }
    .evidence h3 { margin: 0; font-size: 15px; }
    .evidence h4 { margin: 3px 0 0; font-size: 14px; }
    .view-heading, .section-title {
      display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
    }
    .view-heading { padding: 4px 0 18px; border-bottom: 1px solid #cfd5dd; }
    .view-heading-actions { display: flex; align-items: center; gap: 10px; }
    .view-heading-actions button {
      width: auto; min-height: 34px; margin: 0; padding: 7px 10px;
      color: #0057b8; background: #e8f3ff; border: 1px solid rgba(10, 132, 255, .34);
    }
    .section-title { align-items: center; margin-bottom: 14px; }
    .section-title > span, .claim-note { color: var(--muted); font-size: 11px; }
    .evidence-section { padding: 22px 0; border-bottom: 1px solid var(--line); }
    .metric-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px; margin: 0; background: var(--line); border-bottom: 1px solid var(--line); }
    .metric { min-height: 96px; padding: 15px; border: 0; border-radius: 0; background: #fff; }
    .metric .metric-value { font-size: 17px; overflow-wrap: anywhere; }
    .metric .metric-label { font-size: 10px; text-transform: uppercase; }
    .metric .metric-help { font-size: 10px; }
    table { display: table; width: 100%; table-layout: fixed; border-collapse: collapse; background: #fff; border: 1px solid var(--line); }
    th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; vertical-align: top; font-size: 12px; }
    th { background: #f4f6f8; color: #647081; font-size: 10px; text-transform: uppercase; }
    .quality-cell-detail { display: block; margin-top: 6px; color: var(--muted); font-size: 10px; line-height: 1.4; }
    .pipeline { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); border: 1px solid var(--line); background: #fff; }
    .pipeline > div { min-height: 92px; padding: 13px; border-right: 1px solid var(--line); }
    .pipeline > div:last-child { border-right: 0; }
    .pipeline b { display: block; color: #9aa3af; font-size: 10px; }
    .pipeline span { display: block; margin: 10px 0 4px; font-weight: 750; font-size: 13px; }
    .pipeline small { color: var(--muted); font-size: 10px; }
    .claim-ladder { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
    .run-story-ladder { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .run-story-subtitle { margin-top: 18px; }
    .run-story-table small { display: block; margin-top: 5px; color: var(--muted); line-height: 1.35; }
    .run-story-readonly { padding: 10px 12px; border-left: 3px solid var(--purple); background: var(--panel-3); }
    body.read-only main { grid-template-columns: 1fr; }
    .claim-step { min-height: 96px; padding: 14px; border: 1px solid var(--line); border-top: 3px solid #8b95a3; background: #fff; border-radius: 5px; }
    .claim-step.ok { border-top-color: var(--green); }
    .claim-step.warn { border-top-color: #c17b00; }
    .claim-step.bad { border-top-color: var(--red); }
    .claim-step span, .claim-step small { display: block; color: var(--muted); font-size: 10px; }
    .claim-step strong { display: block; margin: 10px 0 4px; font-size: 14px; overflow-wrap: anywhere; }
    .artifact-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .artifact-card { padding: 15px; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
    .artifact-head { display: flex; justify-content: space-between; gap: 12px; }
    .artifact-head span { color: var(--purple); font-size: 10px; font-weight: 800; text-transform: uppercase; }
    .artifact-card p { min-height: 74px; color: #3e4753; font-size: 12px; line-height: 1.55; }
    .artifact-handoff { display: flex; gap: 18px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--muted); font-size: 10px; }
    .artifact-card details { margin-top: 10px; padding: 0; border: 0; }
    .artifact-card code, .provenance code { display: block; margin-top: 7px; color: #66707d; overflow-wrap: anywhere; white-space: normal; font-size: 10px; }
    .provenance { margin-top: 16px; padding: 11px 13px; border: 1px solid var(--line); background: #f7f8fa; }
    .capability-strip { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--line); background: #fff; }
    .capability-strip div { padding: 16px; border-right: 1px solid var(--line); }
    .capability-strip div:last-child { border: 0; }
    .capability-strip b, .capability-strip span { display: block; }
    .capability-strip b { font-size: 21px; }
    .capability-strip span { margin-top: 4px; color: var(--muted); font-size: 10px; }
    .coordination-graph { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr; align-items: center; gap: 8px; }
    .coordination-graph > div { min-height: 78px; padding: 14px; border: 1px solid var(--line); background: #fff; border-radius: 5px; }
    .coordination-graph b, .coordination-graph span { display: block; }
    .coordination-graph span { margin-top: 5px; color: var(--muted); font-size: 10px; }
    .coordination-graph i { color: #8b95a3; font-size: 9px; font-style: normal; text-transform: uppercase; }
    .diagnosis { padding: 14px; border-left: 3px solid var(--accent); background: #f4f7fc; font-size: 13px; }
    .boundary-note { margin: 12px 0 0; color: #596575; font-size: 11px; }
    .evidence-list { display: flex; flex-wrap: wrap; gap: 6px; }
    .evidence-list span { padding: 5px 8px; border: 1px solid var(--line); border-radius: 4px; background: #fff; color: #526071; font-size: 10px; }
    .next-actions { margin: 0; padding-left: 20px; }
    .next-actions li { padding: 4px 0; font-size: 12px; }
    .timeline-lane { margin-bottom: 8px; }
    .run-facts { display: flex; flex-wrap: wrap; gap: 14px; margin: 10px 0 14px; color: var(--muted); font-size: 10px; }
    .event-pill, .legend-item { border-radius: 4px; }
    .empty-inline { padding: 18px; border: 1px dashed #c7cdd5; color: var(--muted); font-size: 12px; }
    .context-jumps { display: flex; flex-wrap: wrap; gap: 8px; }
    .context-jump { min-width: 180px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 5px; background: #fff; color: var(--ink); text-decoration: none; }
    .context-jump b, .context-jump span { display: block; }
    .context-jump span { margin-top: 4px; color: var(--muted); font-size: 10px; }
    .context-turn { margin-top: 10px; scroll-margin-top: 132px; border: 1px solid var(--line); border-radius: 5px; background: #fff; }
    .context-turn > summary { display: grid; grid-template-columns: 110px minmax(220px, 1fr) auto auto; align-items: center; gap: 12px; min-height: 62px; padding: 10px 14px; cursor: pointer; list-style: none; }
    .context-turn > summary::-webkit-details-marker { display: none; }
    .context-turn > summary > span:first-child b, .context-turn > summary > span:first-child small { display: block; }
    .context-turn > summary > span:first-child small { margin-top: 3px; color: var(--accent); font-size: 10px; }
    .context-turn-summary { color: #485466; font-size: 11px; }
    .context-turn-metrics { color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; white-space: nowrap; }
    .context-key { padding: 5px 7px; border: 1px solid #c9d7f6; border-radius: 4px; background: #eef4ff; color: #2758a6; font-size: 9px; }
    .context-turn-body { border-top: 1px solid var(--line); }
    .context-flow-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .context-flow-grid > section { min-width: 0; padding: 14px; border-right: 1px solid var(--line); }
    .context-flow-grid > section:last-child { border-right: 0; }
    .context-stage { display: block; margin-bottom: 10px; color: #657185; font-size: 9px; font-weight: 700; text-transform: uppercase; }
    .context-facts, .context-actions, .context-feedback { margin: 0; padding-left: 18px; }
    .context-facts li, .context-actions li, .context-feedback li { margin: 0 0 7px; color: #303b4a; font-size: 11px; line-height: 1.45; }
    .context-actions li b, .context-actions li span { display: block; }
    .context-actions li span { margin-top: 2px; color: var(--muted); overflow-wrap: anywhere; }
    .context-feedback { padding-left: 0; list-style: none; }
    .context-feedback li { display: grid; grid-template-columns: auto 1fr; align-items: start; gap: 7px; }
    .context-decision { min-height: 38px; margin: 0 0 10px; font-size: 11px; line-height: 1.5; white-space: pre-line; }
    .context-caption { margin: 9px 0 0; color: var(--muted); font-size: 9px; line-height: 1.45; }
    .context-bars { display: grid; gap: 7px; }
    .context-bar-row { display: grid; grid-template-columns: 88px minmax(50px, 1fr) 48px; align-items: center; gap: 6px; font-size: 9px; }
    .context-bar-row > span { color: #4d5969; }
    .context-bar-row > b { text-align: right; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 500; }
    .context-bar-track { height: 7px; overflow: hidden; border-radius: 3px; background: #e8ecf2; }
    .context-bar-track i { display: block; height: 100%; border-radius: 3px; background: #537fcf; }
    .context-technical { border-top: 1px solid var(--line); background: #f8fafc; }
    .context-technical > summary, .context-memory > summary { padding: 10px 14px; color: #4f5d70; cursor: pointer; font-size: 10px; font-weight: 600; }
    .context-technical-grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 14px; padding: 0 14px 14px; }
    .context-technical-grid h4 { margin: 0 0 8px; font-size: 11px; }
    .context-technical-grid table { margin: 0; background: #fff; }
    .context-memory { margin: 0 14px 12px; border: 1px solid var(--line); background: #fff; }
    .context-memory pre { max-height: 280px; margin: 0; padding: 12px; overflow: auto; color: #334052; font-size: 10px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
    .context-technical > .boundary-note { margin: 0; padding: 0 14px 14px; }
    .workspace-toolbar {
      display: grid;
      grid-template-columns: minmax(280px, 0.8fr) minmax(320px, 1.2fr);
      gap: 0;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    .source-picker, .source-picker-meta { min-width: 0; padding: 12px 18px; }
    .source-picker { border-right: 1px solid var(--line); }
    .source-picker label { display: block; margin: 0 0 6px; color: var(--muted); font-size: 10px; font-weight: 800; }
    .source-picker select {
      width: 100%; height: 38px; border: 1px solid #c7cfda; border-radius: 4px;
      background: #fff; color: var(--ink); padding: 0 10px; font: inherit; font-weight: 700;
    }
    .source-picker-meta { display: flex; align-items: center; color: var(--muted); font-size: 11px; line-height: 1.5; }
    .source-identity {
      display: grid; grid-template-columns: minmax(260px, 1fr) auto; gap: 16px;
      margin: 0 0 14px; padding: 18px 20px; border: 1px solid var(--line); background: #fff;
    }
    .source-identity > div > span { color: var(--accent); font-size: 10px; font-weight: 800; text-transform: uppercase; }
    .source-identity h2 { margin: 5px 0 4px; font-size: 20px; }
    .source-identity p { margin: 0; color: var(--muted); font-size: 11px; }
    .source-identity dl { grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr; margin: 0; border-top: 1px solid var(--line); }
    .source-identity dl > div { min-width: 0; padding: 12px 14px 0 0; }
    .source-identity dt { margin-bottom: 4px; color: var(--muted); font-size: 9px; font-weight: 800; }
    .source-identity dd { margin: 0; overflow-wrap: anywhere; font-size: 11px; line-height: 1.45; }
    .source-provenance { grid-column: 1 / -1; margin-top: 2px; color: var(--muted); font-size: 10px; }
    .source-provenance summary { cursor: pointer; }
    .source-provenance code { display: block; margin-top: 7px; overflow-wrap: anywhere; white-space: normal; }
    .overview-reading-path { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .trace-context-unit { margin: 10px 0; border: 1px solid var(--line); background: #fff; }
    .trace-context-unit > summary { display: flex; justify-content: space-between; gap: 14px; padding: 14px 16px; cursor: pointer; }
    .trace-context-unit > summary span { color: var(--muted); font-size: 10px; }
    .trace-context-body { padding: 0 14px 14px; border-top: 1px solid var(--line); }
    @media (max-width: 1100px) {
      .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .pipeline { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .pipeline > div:nth-child(3) { border-right: 0; }
      .pipeline > div:nth-child(-n+3) { border-bottom: 1px solid var(--line); }
      .timeline-phase-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .timeline-phase:nth-child(2) { border-right: 0; }
      .timeline-phase:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
      .context-flow-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .context-flow-grid > section:nth-child(2) { border-right: 0; }
      .context-flow-grid > section:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
    }
    @media (max-width: 900px) {
      header { position: sticky; padding: 10px 12px; flex-direction: row; align-items: center; gap: 8px; }
      header .subtitle, .project-chip, #statusToggle, #focusToggle { display: none; }
      .header-actions { display: flex; }
      main { grid-template-columns: 1fr; }
      section { padding: 0 12px 24px; }
      .status { top: 58px; margin: 0 -12px; padding: 0 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .status .pill:nth-child(n+3) { display: none; }
      .view-tabs { top: 105px; margin: 0 -12px 16px; padding: 0 12px; }
      .context-turn { scroll-margin-top: 210px; }
      .view-tabs .utility { margin-left: 0; }
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .metric { min-height: 88px; }
      .scope-hierarchy, .scenario-grid, .answer-strip, .lab-brief-grid,
      .evidence-artifact-grid, .worker-grid { grid-template-columns: 1fr; }
      .scope-hierarchy > div, .answer-strip > div {
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      .scope-hierarchy > div:last-child, .answer-strip > div:last-child { border-bottom: 0; }
      .lab-question { grid-template-columns: 1fr; }
      .lab-brief-grid > div { border-right: 0; border-bottom: 1px solid var(--line); }
      .lab-brief-grid > div:last-child { border-bottom: 0; }
      .task-summary { grid-template-columns: 1fr; }
      .pipeline, .claim-ladder, .artifact-grid, .capability-strip { grid-template-columns: 1fr; }
      .pipeline > div, .capability-strip div { border-right: 0; border-bottom: 1px solid var(--line); }
      .timeline-phase-grid { grid-template-columns: 1fr; }
      .timeline-phase {
        min-height: 0;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      .timeline-phase:last-child { border-bottom: 0; }
      .timeline-phase > strong { min-height: 0; }
      .context-turn > summary { grid-template-columns: 90px 1fr; }
      .context-turn-metrics, .context-key { grid-column: 2; white-space: normal; }
      .context-flow-grid, .context-technical-grid { grid-template-columns: 1fr; }
      .context-flow-grid > section { border-right: 0; border-bottom: 1px solid var(--line); }
      .context-flow-grid > section:last-child { border-bottom: 0; }
      .coordination-graph { grid-template-columns: 1fr; }
      .coordination-graph i { text-align: center; }
      table { display: block; overflow-x: auto; table-layout: auto; }
      table thead, table tbody { display: table; min-width: 680px; width: 100%; table-layout: auto; }
      th, td { overflow-wrap: normal; word-break: normal; }
      .artifact-card p { min-height: 0; }
      .view-heading { align-items: center; }
      .workspace-toolbar, .source-identity, .source-identity dl { grid-template-columns: 1fr; }
      .source-picker { border-right: 0; border-bottom: 1px solid var(--line); }
      .source-identity dl > div { padding-right: 0; }
      .trace-context-unit > summary { display: grid; }
    }
  </style>
</head>
<body class="read-only status-collapsed">
  <header>
    <div class="brand-lockup">
      <div class="brand-mark">NH</div>
      <div>
        <h1>NanoHarness 证据工作台</h1>
      <div class="subtitle">一次选择运行，逐层读懂 AgentLoop、上下文、控制决策与证据边界</div>
      </div>
    </div>
    <div class="header-actions">
      <button id="statusToggle" onclick="toggleStatusBar()" title="显示或隐藏运行状态">状态</button>
      <button id="focusToggle" onclick="toggleFocusMode()" title="专注查看证据">专注</button>
      <div class="project-chip" id="projectDir"></div>
    </div>
  </header>
  <main>
    <section>
      <div class="status">
        <div class="pill"><div class="k">Python Runtime</div><div class="v" id="python"></div></div>
        <div class="pill"><div class="k">最新运行</div><div class="v" id="latestRun"></div></div>
        <div class="pill"><div class="k">当前运行</div><div class="v" id="currentSource">-</div></div>
        <div class="pill"><div class="k">当前视图</div><div class="v" id="currentView">运行概览</div></div>
      </div>
      <div class="workspace-toolbar">
        <div class="source-picker">
          <label for="sourceSelect">选择运行证据</label>
          <select id="sourceSelect" onchange="changeEvidenceSource()"></select>
        </div>
        <div class="source-picker-meta" id="sourceDescription">正在发现可读取的运行...</div>
      </div>
      <div class="view-tabs">
        <button data-view="overview" onclick="loadEvidence('overview')">运行概览</button>
        <button data-view="timeline" onclick="loadEvidence('timeline')">执行过程</button>
        <button data-view="context" onclick="loadEvidence('context')">上下文与决策</button>
        <button data-view="results" onclick="loadEvidence('results')">结果与证据</button>
        <button class="utility" onclick="refreshWorkspace()" title="重新发现最新运行并刷新">刷新</button>
      </div>
      <div id="output" class="output">正在加载运行证据...</div>
    </section>
  </main>
  <script>
    const evidenceTitles = {
      overview: '运行概览',
      timeline: '执行过程',
      context: '上下文与决策',
      results: '结果与证据'
    };
    let evidenceSources = [];
    let activeSource = '';
    let activeView = 'overview';

    function displayStatus(value) {
      const labels = {
        completed: '已完成',
        passed: '通过',
        blocked: '已阻塞',
        failed: '失败',
        paused: '已暂停',
        not_run: '未运行'
      };
      return String(value || '')
        .split(' · ')
        .map(part => labels[part] || part)
        .join(' · ');
    }

    async function fetchStatus(selectPublishedSource = false) {
      const res = await fetch('/api/status');
      const data = await res.json();
      document.getElementById('projectDir').textContent = data.project_dir;
      document.getElementById('python').textContent = data.python;
      document.getElementById('latestRun').textContent = data.latest_run || '无';
      evidenceSources = data.evidence_sources || [];
      if (selectPublishedSource || !activeSource) {
        activeSource = data.selected_source || (evidenceSources[0] || {}).key || '';
      }
      renderSourceSelector();
      return data;
    }

    function renderSourceSelector() {
      const selector = document.getElementById('sourceSelect');
      selector.innerHTML = '';
      for (const source of evidenceSources) {
        const option = document.createElement('option');
        option.value = source.key;
        option.textContent = source.available
          ? `${source.title} · ${displayStatus(source.status)}`
          : `${source.title} · 尚未运行`;
        option.selected = source.key === activeSource;
        selector.appendChild(option);
      }
      const source = evidenceSources.find(item => item.key === activeSource);
      document.getElementById('currentSource').textContent = source?.title || '-';
      document.getElementById('sourceDescription').textContent = source
        ? `${source.description} · ${source.trace_count} 条 Trace`
        : '没有发现运行证据';
    }

    async function changeEvidenceSource() {
      activeSource = document.getElementById('sourceSelect').value;
      renderSourceSelector();
      await loadEvidence(activeView);
    }

    async function loadEvidence(view) {
      activeView = Object.prototype.hasOwnProperty.call(evidenceTitles, view)
        ? view
        : 'overview';
      const query = new URLSearchParams({source: activeSource, view: activeView});
      const res = await fetch(`/api/evidence?${query.toString()}`);
      const data = await res.json();
      document.getElementById('output').innerHTML = data.html;
      setActiveEvidence(activeView);
    }

    function setActiveEvidence(view) {
      const title = evidenceTitles[view] || view;
      document.getElementById('currentView').textContent = title;
      for (const button of document.querySelectorAll('[data-view]')) {
        button.classList.toggle('active', button.dataset.view === view);
      }
    }

    async function refreshWorkspace() {
      await fetchStatus(true);
      await loadEvidence(activeView);
    }

    function toggleFocusMode() {
      const enabled = document.body.classList.toggle('focus-mode');
      if (enabled) {
        document.body.classList.add('status-collapsed');
      }
      updateLayoutControls();
      return enabled;
    }

    function toggleStatusBar() {
      const collapsed = document.body.classList.toggle('status-collapsed');
      updateLayoutControls();
      return collapsed;
    }

    function updateLayoutControls() {
      const statusHidden = document.body.classList.contains('status-collapsed');
      const focused = document.body.classList.contains('focus-mode');
      const statusToggle = document.getElementById('statusToggle');
      const focusToggle = document.getElementById('focusToggle');
      if (statusToggle) {
        statusToggle.textContent = statusHidden ? '显示状态' : '隐藏状态';
      }
      if (focusToggle) {
        focusToggle.textContent = focused ? '退出专注' : '专注';
      }
    }
    const pageParams = new URLSearchParams(window.location.search);
    if (pageParams.has('focus')) {
      document.body.classList.add('focus-mode', 'status-collapsed');
    }
    async function initializeWorkbench() {
      const requestedView = pageParams.get('view');
      activeView = Object.prototype.hasOwnProperty.call(evidenceTitles, requestedView)
        ? requestedView
        : 'overview';
      updateLayoutControls();
      await fetchStatus(true);
      await loadEvidence(activeView);
    }
    initializeWorkbench();
  </script>
</body>
</html>
"""
