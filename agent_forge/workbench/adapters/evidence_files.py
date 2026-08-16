from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.campaign import CampaignState, summarize_campaign
from agent_forge.observability.api import RunStory, load_run_story
from agent_forge.workbench.domain import EvidenceSource
from agent_forge.workbench.ports import EvidenceCatalogPort
from agent_forge.storage_layout import (
    CAMPAIGN_RUN_ROOT,
    DEBUG_LAB_STATE_ROOT,
    INDEX_ROOT,
    RUNS_ROOT,
    SHOWCASE_RUN_ROOT,
)


class FileEvidenceCatalog(EvidenceCatalogPort):
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir.absolute()

    # 主要入口：把不同目录布局的运行统一成 Workbench 可选择的证据来源。
    def evidence_sources(self) -> tuple[EvidenceSource, ...]:
        """返回“能力类型 → 不可变 Run → Case/Worker”的稳定叶子列表。

        主动面只保留 Lab 1、Lab 2 与 Mini-50。普通 latest 指针和历史实验不再与
        这些权威入口平铺；它们仍可通过 ``forge inspect`` 或归档文件读取。
        """

        return (
            *self._governed_sources(),
            *self._orchestration_sources(),
            *self._mini50_sources(),
        )

    def _governed_sources(self) -> tuple[EvidenceSource, ...]:
        root = self.project_dir / SHOWCASE_RUN_ROOT
        run_roots = []
        if root.is_dir():
            run_roots = [
                path
                for path in root.iterdir()
                if path.is_dir()
                and read_json_file(path / "showcase.json").get("scenario") == "governed"
            ]
        run_roots.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        if not run_roots:
            source = self._governed_source()
            return (
                _with_navigation(
                    source,
                    category_key="governed",
                    category_title="Lab 1 · 持久化控制面",
                    run_key="not-run",
                    run_title="尚未运行",
                ),
            )
        return tuple(
            self._governed_source_from_root(
                path,
                key="governed" if index == 0 else f"governed:{path.name}",
            )
            for index, path in enumerate(run_roots)
        )

    def _governed_source_from_root(self, root: Path, *, key: str) -> EvidenceSource:
        manifest = read_json_file(root / "showcase.json")
        artifact_dir = _safe_local_path(
            self.project_dir,
            str(manifest.get("artifact_dir") or ""),
            require_directory=True,
        )
        traces = tuple(
            (f"Phase {index}", path)
            for index, path in enumerate(
                sorted((root / "phases").glob("*/trace.json")), start=1
            )
        )
        latest_trace = traces[-1][1] if traces else None
        story = self._load_story_if_present(artifact_dir)
        task = str(
            (story.task if story else "")
            or read_json_file(latest_trace).get("task")
            or "受治理人工变更"
        )
        run_title = _run_display_title(root.name)
        return EvidenceSource(
            key=key,
            title=f"Lab 1 · {run_title}",
            description="Human Input、Approval、Checkpoint 与 Operation Ledger",
            source_type="scenario",
            task=task,
            status=str(manifest.get("status") or "not_run"),
            primary_path=root / "showcase.json",
            run_dir=artifact_dir,
            trace_entries=traces,
            usage_path=(
                artifact_dir / "usage.json"
                if artifact_dir is not None and (artifact_dir / "usage.json").is_file()
                else None
            ),
            category_key="governed",
            category_title="Lab 1 · 持久化控制面",
            run_key=root.name,
            run_title=run_title,
            item_key="overview",
            item_title="全部控制步骤",
        )

    def _orchestration_sources(self) -> tuple[EvidenceSource, ...]:
        root = self.project_dir / SHOWCASE_RUN_ROOT
        summaries = sorted(
            root.glob("*/fanout/fanout_summary.json") if root.is_dir() else (),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not summaries:
            source = self._orchestration_source()
            return (
                _with_navigation(
                    source,
                    category_key="orchestration",
                    category_title="Lab 2 · Coordinated Agents",
                    run_key="not-run",
                    run_title="尚未运行",
                ),
            )
        sources: list[EvidenceSource] = []
        for run_index, summary_path in enumerate(summaries):
            summary = read_json_file(summary_path)
            run_root = summary_path.parent.parent
            run_title = _run_display_title(run_root.name)
            traces = _fanout_trace_entries(summary)
            base_key = (
                "orchestration" if run_index == 0 else f"orchestration:{run_root.name}"
            )
            sources.append(
                EvidenceSource(
                    key=base_key,
                    title=f"Lab 2 · {run_title}",
                    description="依赖批次、隔离 Worker、冲突门禁与只读 Finalizer",
                    source_type="scenario",
                    task=str(summary.get("goal") or "Coordinated Agent workflow"),
                    status=str(summary.get("status") or "not_run"),
                    primary_path=summary_path,
                    run_dir=run_root,
                    trace_entries=traces,
                    category_key="orchestration",
                    category_title="Lab 2 · Coordinated Agents",
                    run_key=run_root.name,
                    run_title=run_title,
                    item_key="overview",
                    item_title="整体编排",
                )
            )
            for item_index, (label, trace_path) in enumerate(traces, start=1):
                usage_path = trace_path.parent / "usage.json"
                sources.append(
                    EvidenceSource(
                        key=f"{base_key}:worker:{item_index}",
                        title=f"{run_title} · {label}",
                        description="单个 Worker/Finalizer 的模型、工具与结果证据",
                        source_type="scenario-worker",
                        task=str(read_json_file(trace_path).get("task") or label),
                        status=str(summary.get("status") or "not_run"),
                        primary_path=trace_path,
                        run_dir=trace_path.parent,
                        trace_entries=((label, trace_path),),
                        usage_path=usage_path if usage_path.is_file() else None,
                        category_key="orchestration",
                        category_title="Lab 2 · Coordinated Agents",
                        run_key=run_root.name,
                        run_title=run_title,
                        item_key=f"worker-{item_index}",
                        item_title=label,
                    )
                )
        return tuple(sources)

    def _mini50_sources(self) -> tuple[EvidenceSource, ...]:
        canonical = self._benchmark_source()
        canonical_summary = read_json_file(canonical.primary_path)
        evaluation = canonical_summary.get("canonical_evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        canonical_root = _safe_local_path(
            self.project_dir,
            str(evaluation.get("evidence_run_dir") or ""),
            require_directory=True,
        )
        run_roots = _mini50_run_roots(self.project_dir)
        if canonical_root is not None:
            run_roots = [
                canonical_root,
                *[root for root in run_roots if root != canonical_root],
            ]
        if not run_roots:
            return (
                _with_navigation(
                    canonical,
                    category_key="evaluation",
                    category_title="Mini-50 · 真实仓库能力评测",
                    run_key="canonical",
                    run_title="Canonical Mini-50",
                ),
            )
        sources: list[EvidenceSource] = []
        for run_index, run_root in enumerate(run_roots):
            records, run_status = _mini50_records(run_root)
            if len(records) != 50:
                continue
            run_title = _run_display_title(run_root.name)
            base_key = "evaluation" if run_index == 0 else f"evaluation:{run_root.name}"
            overall_primary = (
                canonical.primary_path
                if run_root == canonical_root
                else _first_existing(
                    run_root / "campaign_summary.json",
                    run_root / "combined_result.json",
                    run_root / "campaign.json",
                )
            )
            trace_entries = tuple(
                entry
                for record in records
                if (entry := _mini50_trace_entry(self.project_dir, record)) is not None
            )
            sources.append(
                EvidenceSource(
                    key=base_key,
                    title=f"Mini-50 · {run_title}",
                    description="固定 50 Case 的 Pass@1、Official 与完整分母",
                    source_type="benchmark",
                    task="SWE-bench Verified Mini-50",
                    status=run_status,
                    primary_path=overall_primary,
                    run_dir=run_root,
                    trace_entries=trace_entries,
                    category_key="evaluation",
                    category_title="Mini-50 · 真实仓库能力评测",
                    run_key=run_root.name,
                    run_title=run_title,
                    item_key="overview",
                    item_title="50 Case 总览",
                )
            )
            for ordinal, record in enumerate(records, start=1):
                case_id = str(
                    record.get("case_id")
                    or record.get("instance_id")
                    or f"case-{ordinal}"
                )
                run_dir = _safe_local_path(
                    self.project_dir,
                    str(record.get("run_dir") or ""),
                    require_directory=True,
                )
                entry = _mini50_trace_entry(self.project_dir, record)
                if run_dir is None or entry is None:
                    continue
                label, trace_path = entry
                usage_path = trace_path.parent / "usage.json"
                case_status = _mini50_case_status(record)
                sources.append(
                    EvidenceSource(
                        key=f"{base_key}:case:{ordinal}",
                        title=case_id,
                        description="单个真实 Case 的 Trace、Tool、Patch 与 Official 状态",
                        source_type="benchmark-case",
                        task=case_id,
                        status=case_status,
                        primary_path=_first_existing(run_dir / "results.json", run_dir),
                        run_dir=run_dir,
                        trace_entries=((label, trace_path),),
                        usage_path=usage_path if usage_path.is_file() else None,
                        category_key="evaluation",
                        category_title="Mini-50 · 真实仓库能力评测",
                        run_key=run_root.name,
                        run_title=run_title,
                        item_key=case_id,
                        item_title=f"{ordinal:02d} · {case_id} · {_display_case_status(case_status)}",
                    )
                )
        return tuple(sources) or (
            _with_navigation(
                canonical,
                category_key="evaluation",
                category_title="Mini-50 · 真实仓库能力评测",
                run_key="canonical",
                run_title="Canonical Mini-50",
            ),
        )

    @staticmethod
    def _same_run(left: EvidenceSource, right: EvidenceSource) -> bool:
        """判断两个选择项是否最终指向同一个不可变 Run 目录。"""

        if left.run_dir is None or right.run_dir is None:
            return False
        return left.run_dir.resolve() == right.run_dir.resolve()

    def _latest_runtime_source(self) -> EvidenceSource:
        """把 ``run.txt`` 指向的任意运行接入公共视图，而非只支持预置场景。"""

        pointer = self.project_dir / INDEX_ROOT / "run.txt"
        run_dir = self._run_dir_from_pointer(pointer)
        story = self._load_story_if_present(run_dir)
        trace_path = run_dir / "trace.json" if run_dir is not None else None
        trace = read_json_file(trace_path)
        usage_path = run_dir / "usage.json" if run_dir is not None else None
        return EvidenceSource(
            key="latest",
            title="最近一次 Runtime 运行",
            description="任何通过 Harness 或 forge 发布的 Single-Run 证据",
            source_type="runtime",
            task=str((story.task if story else "") or trace.get("task") or "尚未运行"),
            status=str(
                (story.status if story else "")
                or trace.get("status")
                or trace.get("stop_reason")
                or "not_run"
            ),
            primary_path=run_dir,
            run_dir=run_dir,
            trace_entries=(
                (("AgentLoop", trace_path),)
                if trace_path is not None and trace_path.is_file()
                else ()
            ),
            usage_path=(
                usage_path if usage_path is not None and usage_path.is_file() else None
            ),
        )

    def _governed_source(self) -> EvidenceSource:
        run_dir = self.latest_governed_run_dir()
        trace_path = self.latest_governed_trace_path()
        story = self._load_story_if_present(run_dir)
        trace = read_json_file(trace_path)
        return EvidenceSource(
            key="governed",
            title="受治理单 Agent",
            description="人工授权、操作状态、Checkpoint 与防重复恢复",
            source_type="scenario",
            task=str((story.task if story else "") or trace.get("task") or "尚未运行"),
            status=str(
                (story.status if story else "")
                or trace.get("status")
                or trace.get("stop_reason")
                or "not_run"
            ),
            primary_path=run_dir,
            run_dir=run_dir,
            trace_entries=(("AgentLoop", trace_path),) if trace_path else (),
            usage_path=self.latest_governed_usage_path(),
        )

    def _orchestration_source(self) -> EvidenceSource:
        summary_path = self.latest_orchestration_fanout_path()
        summary = read_json_file(summary_path)
        trace_entries: list[tuple[str, Path]] = []
        for result in summary.get("results") or []:
            if not isinstance(result, dict):
                continue
            trace_path = Path(str(result.get("trace_path") or ""))
            if trace_path.is_file():
                trace_entries.append(
                    (f"Worker · {result.get('task_id') or '未命名任务'}", trace_path)
                )
        finalizer_value = str(summary.get("finalizer_trace_path") or "")
        finalizer_path = Path(finalizer_value) if finalizer_value else None
        if finalizer_path is not None and finalizer_path.is_file():
            trace_entries.append(("Finalizer · 合并后验证", finalizer_path))
        usage_value = str(summary.get("finalizer_usage_path") or "")
        usage_path = Path(usage_value) if usage_value else None
        run_dir = summary_path.parent.parent if summary_path is not None else None
        return EvidenceSource(
            key="orchestration",
            title="并行多 Agent",
            description="依赖批次、隔离 Worker、冲突门禁与只读 Finalizer",
            source_type="scenario",
            task=str(summary.get("goal") or "尚未运行"),
            status=str(summary.get("status") or "not_run"),
            primary_path=summary_path,
            run_dir=run_dir,
            trace_entries=tuple(trace_entries),
            usage_path=usage_path if usage_path and usage_path.is_file() else None,
        )

    def _benchmark_source(self) -> EvidenceSource:
        canonical_path = self.canonical_showcase_summary_path()
        canonical_summary = read_json_file(canonical_path)
        if canonical_summary.get("artifact_type") == "canonical_showcase":
            return self._canonical_showcase_source(
                canonical_path,
                canonical_summary,
            )

        return self._historical_benchmark_source(source_key="evaluation")

    def _historical_benchmark_source(self, *, source_key: str) -> EvidenceSource:
        """读取旧质量摘要或 Campaign，供兼容与显式历史下钻使用。"""

        quality_path = self.runtime_quality_summary_path()
        quality_summary = read_json_file(quality_path)
        if quality_summary:
            return self._runtime_quality_source(
                quality_path,
                quality_summary,
                source_key=source_key,
            )

        incident_path = self.quality_selection_incident_path()
        incident = read_json_file(incident_path)
        if incident.get("artifact_type") == "quality_selection_incident":
            return self._quality_selection_incident_source(
                incident_path,
                incident,
                source_key=source_key,
            )

        campaign_dir = self.latest_campaign_dir()
        campaign_state = self.latest_campaign_state()
        campaign_summary = self.latest_campaign_summary()
        benchmark_run = self.latest_benchmark_run_dir()
        trace_entries: list[tuple[str, Path]] = []
        if benchmark_run is not None:
            for trace_path in sorted(benchmark_run.glob("cases/**/trace.json")):
                relative = trace_path.relative_to(benchmark_run)
                trace_entries.append((str(relative.parent), trace_path))
        status = str(
            campaign_state.get("status")
            or campaign_summary.get("status")
            or ("published" if campaign_dir else "not_run")
        )
        task = str(
            campaign_state.get("name")
            or campaign_state.get("campaign_id")
            or campaign_summary.get("campaign_id")
            or "Harness 配置配对评测"
        )
        campaign_config = campaign_state.get("config")
        if not isinstance(campaign_config, dict):
            campaign_config = {}
        case_count = len(campaign_config.get("case_ids") or [])
        variant_count = len(campaign_config.get("variants") or [])
        title = (
            f"SWE-bench 样本评测 · {case_count} Case × {variant_count} 配置"
            if case_count and variant_count
            else "SWE-bench 样本评测"
        )
        if source_key == "evaluation-history":
            title = f"历史归档 · {title}"
        return EvidenceSource(
            key=source_key,
            title=title,
            description=(
                "历史固定样本、配对运行、官方结果、成本与失败分布；不作为当前 headline"
                if source_key == "evaluation-history"
                else "固定样本、配对运行、官方结果、成本与失败分布"
            ),
            source_type="benchmark",
            task=task,
            status=status,
            primary_path=campaign_dir,
            run_dir=benchmark_run,
            trace_entries=tuple(trace_entries),
            usage_path=self.latest_benchmark_usage_path(),
        )

    def _canonical_showcase_source(
        self,
        summary_path: Path | None,
        summary: dict[str, Any],
    ) -> EvidenceSource:
        """把当前展示协议作为默认评测入口，不把历史实验提升为 headline。"""

        evaluation_value = summary.get("canonical_evaluation")
        evaluation = evaluation_value if isinstance(evaluation_value, dict) else {}
        profile_value = summary.get("current_profile")
        profile = profile_value if isinstance(profile_value, dict) else {}
        evidence_run_dir = str(evaluation.get("evidence_run_dir") or "").strip()
        run_dir = self.project_dir / evidence_run_dir if evidence_run_dir else None
        if run_dir is not None and (
            not _is_under(run_dir, self.project_dir) or not run_dir.is_dir()
        ):
            run_dir = None
        trace_entries: list[tuple[str, Path]] = []
        usage_paths: list[Path] = []
        if run_dir is not None:
            for trace_path in sorted(run_dir.glob("cases/**/trace.json")):
                trace_entries.append(
                    (str(trace_path.parent.relative_to(run_dir)), trace_path)
                )
            usage_paths.extend(sorted(run_dir.glob("cases/**/usage.json")))

        profile_id = str(
            profile.get("profile_id") or summary.get("showcase_id") or "未命名"
        )
        return EvidenceSource(
            key="evaluation",
            title=str(summary.get("title") or "NanoHarness Canonical Showcase"),
            description=(
                "当前公开质量观测、证据边界与下一轮确认实验；"
                "历史选型和失败实验不进入主动展示"
            ),
            source_type="benchmark",
            task=str(
                summary.get("question")
                or f"用 {profile_id} 完成可复核的 Pass@1 official 评测"
            ),
            status=str(summary.get("status") or evaluation.get("status") or "pending"),
            primary_path=summary_path,
            run_dir=run_dir,
            trace_entries=tuple(trace_entries),
            usage_path=usage_paths[0] if usage_paths else None,
        )

    def _quality_selection_incident_source(
        self,
        incident_path: Path | None,
        incident: dict[str, Any],
        *,
        source_key: str,
    ) -> EvidenceSource:
        """展示失败关闭的选型记录，但不挂载原始 Case Trace。"""

        incident_facts = incident.get("incident")
        if not isinstance(incident_facts, dict):
            incident_facts = {}
        decision = incident.get("decision")
        if not isinstance(decision, dict):
            decision = {}
        planned = int(incident_facts.get("planned_case_starts") or 0)
        contaminated = int(incident_facts.get("uniformly_contaminated_tail_slots") or 0)
        title = str(incident.get("title") or "Quality Selection Fail-Closed")
        if source_key == "evaluation-history":
            title = f"历史归档 · {title}"
        return EvidenceSource(
            key=source_key,
            title=title,
            description="失败关闭事故；只证明实验纪律，不作为当前质量 headline",
            source_type="benchmark",
            task=str(
                incident.get("question") or "为什么这次质量选型没有产生可发布 winner？"
            ),
            status=(
                f"{incident.get('status') or 'invalid_no_winner'}"
                f" · {planned} finalized · tail {contaminated} contaminated"
                f" · summarizer exit {decision.get('summarizer_exit_code', 'unknown')}"
            ),
            primary_path=incident_path,
            run_dir=None,
            trace_entries=(),
            usage_path=None,
        )

    def _runtime_quality_source(
        self,
        quality_path: Path | None,
        quality_summary: dict[str, Any],
        *,
        source_key: str = "evaluation",
    ) -> EvidenceSource:
        """把发布的质量实验摘要和本机原始 Trace 组合成一个证据入口。

        Git 仓库只提交小型、可审计的实验摘要；开发机若仍保留 ``.agent_forge``
        原始运行目录，Workbench 再按摘要中的相对路径补充 Turn 级下钻证据。
        缺少本地 Trace 不影响公开结果页，也不会伪造运行过程。
        """

        schema_version = int(quality_summary.get("schema_version") or 1)
        formal_summary = schema_version >= 2
        phase2_value = quality_summary.get("phase2")
        phase2 = phase2_value if isinstance(phase2_value, dict) else {}
        phase2_summary = schema_version >= 3 and bool(phase2)
        iterations = [
            item
            for item in quality_summary.get("iterations") or []
            if isinstance(item, dict)
        ]
        reference_iteration = str(
            quality_summary.get("reference_iteration")
            or (iterations[0].get("id") if formal_summary and iterations else "")
        )
        trace_entries: list[tuple[str, Path]] = []
        usage_paths: list[Path] = []
        accepted_run_dirs: list[Path] = []
        if phase2_summary:
            run_dir_groups_value = phase2.get("evidence_run_dirs")
            if isinstance(run_dir_groups_value, dict):
                run_dir_groups = [
                    (str(group_name), values)
                    for group_name, values in run_dir_groups_value.items()
                ]
            else:
                run_dir_groups = [("evidence", run_dir_groups_value or [])]
            for group_name, values in run_dir_groups:
                run_dir_values = values if isinstance(values, list) else [values]
                for run_dir_value in run_dir_values:
                    run_dir_text = str(run_dir_value or "").strip()
                    if not run_dir_text:
                        continue
                    run_dir = self.project_dir / run_dir_text
                    if not _is_under(run_dir, self.project_dir) or not run_dir.is_dir():
                        continue
                    accepted_run_dirs.append(run_dir)
                    for trace_path in sorted(run_dir.glob("cases/*/trace.json")):
                        trace_entries.append(
                            (
                                f"Phase 2 · {group_name} · {trace_path.parent.name}",
                                trace_path,
                            )
                        )
                    usage_paths.extend(sorted(run_dir.glob("cases/*/usage.json")))
        else:
            for iteration in iterations:
                if str(iteration.get("id") or "") != reference_iteration:
                    continue
                for run_dir_value in iteration.get("run_dirs") or []:
                    run_dir = self.project_dir / str(run_dir_value)
                    if not run_dir.is_dir():
                        continue
                    accepted_run_dirs.append(run_dir)
                    for trace_path in sorted(run_dir.glob("cases/*/trace.json")):
                        trace_entries.append((trace_path.parent.name, trace_path))
                    usage_paths.extend(sorted(run_dir.glob("cases/*/usage.json")))

        metrics = quality_summary.get("reference_metrics") if formal_summary else None
        if not isinstance(metrics, dict):
            metrics = {}
        if not metrics:
            for iteration in iterations:
                if str(iteration.get("id") or "") == reference_iteration:
                    value = iteration.get("metrics")
                    metrics = value if isinstance(value, dict) else {}
                    break
        resolved = int(
            metrics.get("confirmed_solved") or metrics.get("official_resolved") or 0
        )
        case_count = int(
            metrics.get("planned")
            or metrics.get("case_count")
            or metrics.get("official_denominator")
            or 0
        )
        if metrics.get("official_decided") is not None:
            decided = int(metrics.get("official_decided") or 0)
        elif metrics.get("decided") is not None:
            decided = int(metrics.get("decided") or 0)
        elif any(
            name in metrics
            for name in (
                "official_resolved",
                "confirmed_solved",
                "official_unresolved",
                "confirmed_unresolved",
            )
        ):
            unresolved: Any = metrics.get("official_unresolved")
            if unresolved is None:
                unresolved = metrics.get("confirmed_unresolved")
            decided = resolved + int(unresolved or 0)
        else:
            decided = max(
                0,
                case_count
                - int(metrics.get("not_adjudicated") or 0)
                - int(metrics.get("official_empty_or_skipped") or 0)
                - int(metrics.get("official_infrastructure_error") or 0),
            )
        status = str(quality_summary.get("status") or "not_run")
        if not formal_summary:
            status = "exploratory_only · 旧摘要不具备正式 official 基线"
        elif phase2_summary:
            phase2_status = str(
                phase2.get("decision") or phase2.get("status") or "pending"
            )
            status = f"Phase 2 · {phase2_status}"
        elif status == "completed" and case_count:
            status = (
                f"completed · 正式 {reference_iteration} 解决 {resolved}/{case_count}"
                f" · 裁决覆盖 {decided}/{case_count}"
            )
        title = str(
            phase2.get("title")
            or quality_summary.get("title")
            or "Runtime 质量优化实验"
        )
        if source_key == "evaluation-history":
            title = f"历史归档 · {title}"
        return EvidenceSource(
            key=source_key,
            title=title,
            description=(
                "历史实验归档；不作为当前 headline"
                if source_key == "evaluation-history"
                else "Phase 2 个案机制、正确性 Guards 与 Golden-10 扩展证据"
                if phase2_summary
                else (
                    "固定样本、失败驱动迭代、正确性与效率证据"
                    if formal_summary
                    else "Pre-R0 探索性过程证据；旧 accepted 结论已撤回"
                )
            ),
            source_type="benchmark",
            task=str(
                phase2.get("question")
                or quality_summary.get("question")
                or "功能冻结后，Runtime 如何基于失败证据提高质量？"
            ),
            status=status,
            primary_path=quality_path,
            run_dir=accepted_run_dirs[0] if accepted_run_dirs else None,
            trace_entries=tuple(trace_entries),
            usage_path=usage_paths[0] if usage_paths else None,
        )

    def runtime_quality_summary_path(self) -> Path | None:
        """返回公开、稳定的 Runtime 质量实验摘要。"""

        path = self.project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
        return path if path.is_file() else None

    def quality_selection_incident_path(self) -> Path | None:
        """返回只用于历史下钻、永不提升为 headline 的失败关闭记录。"""

        path = (
            self.project_dir
            / "benchmarks"
            / "archive"
            / "legacy-benchmarks"
            / "archive"
            / "quality-selection-v1-fail-closed.json"
        )
        return path if path.is_file() else None

    def canonical_showcase_summary_path(self) -> Path | None:
        """返回当前 canonical 展示摘要；存在时覆盖历史实验的默认优先级。"""

        path = (
            self.project_dir / "benchmarks" / "showcase" / "canonical-showcase-v1.json"
        )
        return path if path.is_file() else None

    @staticmethod
    def _load_story_if_present(run_dir: Path | None) -> RunStory | None:
        if run_dir is None or not (run_dir / "run_manifest.json").is_file():
            return None
        try:
            return load_run_story(run_dir)
        except (OSError, ValueError):
            return None

    def latest_run_dir(self) -> Path | None:
        latest = self.project_dir / INDEX_ROOT
        runs_dir = self.project_dir / RUNS_ROOT
        candidates: list[Path] = []
        bench_run = self._run_dir_from_pointer(latest / "bench.txt")
        if bench_run and _is_under(bench_run, runs_dir):
            candidates.append(bench_run)
        latest_run = self._run_dir_from_pointer(latest / "run.txt")
        if latest_run and _is_under(latest_run, runs_dir):
            bench_pointer = latest / "bench.txt"
            run_pointer = latest / "run.txt"
            if (
                not bench_pointer.exists()
                or run_pointer.stat().st_mtime >= bench_pointer.stat().st_mtime
            ):
                # run.txt 是 Single-Run 发布合同；Debug Lab/项目展示必须回放刚发布的
                # continuation artifact，而不是被旧目录的异常 mtime 抢走。
                return latest_run
            candidates.append(latest_run)
        if runs_dir.exists():
            candidates.extend(path for path in runs_dir.iterdir() if path.is_dir())
        if candidates:
            unique = {path.resolve(): path for path in candidates}
            return max(unique.values(), key=lambda path: path.stat().st_mtime)
        return latest_run

    def latest_run_story(self) -> RunStory | None:
        """当最新运行已发布规范清单时，加载统一 Run Story 读模型。"""

        run_dir = self.latest_run_dir()
        if run_dir is None or not (run_dir / "run_manifest.json").is_file():
            return None
        return load_run_story(run_dir)

    def latest_governed_run_dir(self) -> Path | None:
        """返回受治理恢复场景，不被随后执行的多 Agent 场景覆盖。"""

        pointer = self.project_dir / DEBUG_LAB_STATE_ROOT / "control_artifact.txt"
        run_dir = self._run_dir_from_pointer(pointer)
        runs_dir = self.project_dir / RUNS_ROOT
        if run_dir is not None and _is_under(run_dir, runs_dir):
            return run_dir
        return None

    def latest_governed_run_story(self) -> RunStory | None:
        """加载受治理恢复场景的标准 Run Story。"""

        run_dir = self.latest_governed_run_dir()
        if run_dir is None or not (run_dir / "run_manifest.json").is_file():
            return None
        return load_run_story(run_dir)

    def latest_governed_trace_path(self) -> Path | None:
        """返回受治理恢复场景的 Trace。"""

        return _latest_artifact_in_run(
            self.latest_governed_run_dir(),
            direct_name="trace.json",
            nested_pattern="cases/**/trace.json",
        )

    def latest_governed_usage_path(self) -> Path | None:
        """返回受治理恢复场景的 Usage 证据。"""

        return _latest_artifact_in_run(
            self.latest_governed_run_dir(),
            direct_name="usage.json",
            nested_pattern="cases/**/usage.json",
        )

    def latest_report_path(self) -> str:
        run_dir = self.latest_run_dir()
        if run_dir:
            for name in (
                "report.md",
                "fanout/fanout_report.md",
                "multi_agent/multi_agent_report.md",
                "usage_report.md",
            ):
                candidate = run_dir / name
                if candidate.exists():
                    return str(candidate)
        return ""

    def read_latest_report(self) -> str:
        path = self.latest_report_path()
        if not path:
            return "No report yet. Run DeepSeek Agent Run or SWE-bench Sample first."
        return Path(path).read_text(encoding="utf-8")

    def latest_trace_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        direct = run_dir / "trace.json"
        if direct.exists():
            return direct
        traces = sorted(run_dir.glob("cases/**/trace.json"))
        return max(traces, key=lambda path: path.stat().st_mtime) if traces else None

    def latest_usage_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        direct = run_dir / "usage.json"
        if direct.exists():
            return direct
        usages = sorted(run_dir.glob("cases/**/usage.json"))
        return max(usages, key=lambda path: path.stat().st_mtime) if usages else None

    def latest_comparison_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        candidates = [run_dir / "comparison.json"]
        candidates.extend(sorted(run_dir.glob("cases/*/comparison.json")))
        candidates.extend(sorted(run_dir.glob("cases/*/*/comparison.json")))
        return _newest_existing(candidates)

    def latest_multi_agent_summary_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        candidates = [run_dir / "multi_agent/multi_agent_summary.json"]
        candidates.extend(
            sorted(run_dir.glob("cases/**/multi_agent/multi_agent_summary.json"))
        )
        return _newest_existing(candidates)

    def latest_fanout_summary_path(self) -> Path | None:
        run_dir = self.latest_run_dir()
        if not run_dir:
            return None
        candidate = run_dir / "fanout" / "fanout_summary.json"
        return candidate if candidate.exists() else None

    def latest_orchestration_summary_path(self) -> Path | None:
        """返回最近一次多 Agent 证据，不受当前 Single-Run 指针影响。"""

        runs_dir = self.project_dir / RUNS_ROOT
        candidates: list[Path] = []
        current = self.latest_multi_agent_summary_path()
        if current is not None:
            candidates.append(current)
        if runs_dir.exists():
            candidates.extend(runs_dir.glob("**/multi_agent_summary.json"))
        return _newest_existing(candidates)

    def latest_orchestration_fanout_path(self) -> Path | None:
        """返回最近一次并行 Fanout 证据，不受其他 Lab 的运行顺序影响。"""

        runs_dir = self.project_dir / RUNS_ROOT
        candidates: list[Path] = []
        current = self.latest_fanout_summary_path()
        if current is not None:
            candidates.append(current)
        if runs_dir.exists():
            candidates.extend(runs_dir.glob("**/fanout_summary.json"))
        return _newest_existing(candidates)

    def latest_benchmark_run_dir(self) -> Path | None:
        """返回最近一次 SWE-bench 运行，和交互式 Single-Run 分开选取。"""

        latest = self.project_dir / INDEX_ROOT / "bench.txt"
        runs_dir = self.project_dir / RUNS_ROOT
        pointed = self._run_dir_from_pointer(latest)
        if pointed is not None and (pointed / "results.json").is_file():
            return pointed
        candidates = (
            [path.parent for path in runs_dir.glob("**/results.json")]
            if runs_dir.exists()
            else []
        )
        return _newest_existing(candidates)

    def latest_benchmark_comparison_path(self) -> Path | None:
        """返回最近 Benchmark Run 内的单/多 Agent 对比证据。"""

        run_dir = self.latest_benchmark_run_dir()
        if run_dir is None:
            return None
        candidates = [run_dir / "comparison.json"]
        candidates.extend(run_dir.glob("cases/**/comparison.json"))
        return _newest_existing(candidates)

    def latest_benchmark_multi_agent_summary_path(self) -> Path | None:
        """返回最近 Benchmark Run 内的多 Agent 摘要。"""

        run_dir = self.latest_benchmark_run_dir()
        if run_dir is None:
            return None
        return _newest_existing(list(run_dir.glob("cases/**/multi_agent_summary.json")))

    def latest_benchmark_usage_path(self) -> Path | None:
        """返回最近 Benchmark Run 内的用量证据。"""

        run_dir = self.latest_benchmark_run_dir()
        if run_dir is None:
            return None
        candidates = [run_dir / "usage.json"]
        candidates.extend(run_dir.glob("cases/**/usage.json"))
        return _newest_existing(candidates)

    def trace_paths(self) -> list[tuple[str, Path]]:
        run_dir = self.latest_run_dir()
        if run_dir is None:
            return []
        direct = run_dir / "trace.json"
        if direct.exists():
            return [("AgentLoop", direct)]
        traces = list(run_dir.glob("cases/**/trace.json"))

        def trace_order(path: Path) -> tuple[int, float]:
            parts = set(path.parts)
            priority = 0 if "multi" in parts else 1 if "single" in parts else 2
            return priority, -path.stat().st_mtime

        labelled: list[tuple[str, Path]] = []
        seen_labels: set[str] = set()
        for path in sorted(traces, key=trace_order):
            if "multi" in path.parts:
                label = "多 Agent Runtime"
            elif "single" in path.parts:
                label = "单 Agent Runtime"
            else:
                label = trace_scope_label(path)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            labelled.append((label, path))
        return labelled

    def latest_feedback_path(self) -> Path | None:
        trace_path = self.latest_trace_path()
        run_dir = self.latest_run_dir()
        candidates: list[Path] = []
        if trace_path is not None:
            candidates.append(trace_path.parent / "feedback.json")
        if run_dir is not None:
            candidates.append(run_dir / "feedback.json")
            candidates.extend(run_dir.glob("cases/**/feedback.json"))
        return _newest_existing(candidates)

    def latest_feedback_outcome(self) -> str:
        feedback = read_json_file(self.latest_feedback_path())
        return str(feedback.get("outcome") or "unreviewed")

    def latest_result_record(self) -> dict[str, Any]:
        run_dir = self.latest_benchmark_run_dir()
        if run_dir is None:
            return {}
        results = read_json_file(run_dir / "results.json")
        case_results = results.get("case_results") or []
        return (
            case_results[0]
            if case_results and isinstance(case_results[0], dict)
            else {}
        )

    def latest_campaign_dir(self) -> Path | None:
        """优先返回显式发布的 Campaign，避免工作台悄悄切换实验批次。"""

        latest = self.project_dir / INDEX_ROOT / "campaign.txt"
        campaigns = self.project_dir / CAMPAIGN_RUN_ROOT
        pointed = self._run_dir_from_pointer(latest)
        if pointed is not None:
            return pointed
        candidates: list[Path] = []
        if campaigns.exists():
            candidates.extend(path for path in campaigns.iterdir() if path.is_dir())
        return _newest_existing(candidates)

    def latest_campaign_state(self) -> dict[str, Any]:
        directory = self.latest_campaign_dir()
        if directory is None:
            return {}
        live_state = read_json_file(directory / "campaign.json")
        if live_state:
            return live_state
        # 公开发布包使用 manifest.json；语义与运行中的 campaign.json 相同。
        return read_json_file(directory / "manifest.json")

    def latest_campaign_summary(self) -> dict[str, Any]:
        directory = self.latest_campaign_dir()
        if directory is None:
            return {}
        summary = read_json_file(directory / "campaign_summary.json")
        if not summary:
            # 完成后的可发布 bundle 使用更短的 summary.json 文件名。
            summary = read_json_file(directory / "summary.json")
        if summary or directory is None:
            return summary
        state = self.latest_campaign_state()
        return summarize_campaign(CampaignState.from_dict(state)) if state else {}

    def latest_improvement_record_path(self) -> Path | None:
        """返回当前 campaign 的问题到决策闭环记录。"""

        directory = self.latest_campaign_dir()
        if directory is None:
            return None
        path = directory / "improvement_record.json"
        return path if path.is_file() else None

    def _run_dir_from_pointer(self, pointer: Path) -> Path | None:
        if not pointer.exists():
            return None
        run_dir = Path(pointer.read_text(encoding="utf-8").strip())
        if not run_dir.is_absolute():
            run_dir = self.project_dir / run_dir
        return run_dir if run_dir.exists() else None


def read_json_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "path": str(path)}
    return data if isinstance(data, dict) else {}


def trace_scope_label(trace_path: Path | None) -> str:
    if not trace_path:
        return "unknown trace"
    parts = set(trace_path.parts)
    text = str(trace_path)
    if "verify" in parts:
        return "verify smoke trace"
    if "multi" in parts or "__multi" in text:
        return "multi-agent trace"
    if "single" in parts or "__single" in text:
        return "single-agent trace"
    return "agent run trace"


def _newest_existing(candidates: list[Path]) -> Path | None:
    existing = [path for path in candidates if path.exists()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _latest_artifact_in_run(
    run_dir: Path | None,
    *,
    direct_name: str,
    nested_pattern: str,
) -> Path | None:
    """优先读取运行根目录产物，兼容 Benchmark 的 Case 子目录。"""

    if run_dir is None:
        return None
    direct = run_dir / direct_name
    if direct.is_file():
        return direct
    return _newest_existing(list(run_dir.glob(nested_pattern)))


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _with_navigation(
    source: EvidenceSource,
    *,
    category_key: str,
    category_title: str,
    run_key: str,
    run_title: str,
) -> EvidenceSource:
    """为旧式来源补齐三级导航字段，不复制证据读取逻辑。"""

    return replace(
        source,
        category_key=category_key,
        category_title=category_title,
        run_key=run_key,
        run_title=run_title,
        item_key=source.item_key or "overview",
        item_title=source.item_title or "整体运行",
    )


def _safe_local_path(
    project_dir: Path,
    value: str,
    *,
    require_directory: bool = False,
) -> Path | None:
    """只接纳项目目录内的真实路径，避免展示层跟随外部或逃逸路径。"""

    text = value.strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if not _is_under(candidate, project_dir):
        return None
    if require_directory and not candidate.is_dir():
        return None
    return candidate


def _run_display_title(name: str) -> str:
    """把磁盘唯一名投影成人能当场辨认的 Run 标题。"""

    parts = name.split("__")
    label = re.sub(r"[-_]+", " ", parts[0]).strip() or "run"
    if len(parts) >= 2 and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", parts[1]
    ):
        date, clock = parts[1].split("_", maxsplit=1)
        timestamp = f"{date} {clock.replace('-', ':')}"
        suffix = f" · {parts[2]}" if len(parts) >= 3 and parts[2] else ""
        return f"{label} · {timestamp}{suffix}"
    legacy = re.search(r"(20\d{2})(\d{2})(\d{2})[-_](\d{2})(\d{2})(\d{2})", name)
    if legacy:
        timestamp = (
            f"{legacy.group(1)}-{legacy.group(2)}-{legacy.group(3)} "
            f"{legacy.group(4)}:{legacy.group(5)}:{legacy.group(6)}"
        )
        return f"{label} · {timestamp}"
    return label


def _fanout_trace_entries(summary: dict[str, Any]) -> tuple[tuple[str, Path], ...]:
    entries: list[tuple[str, Path]] = []
    for item in summary.get("results") or summary.get("role_results") or []:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("trace_path") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.is_file():
            label = str(item.get("task_id") or item.get("role") or path.parent.name)
            entries.append((label, path))
    finalizer = summary.get("finalizer")
    if isinstance(finalizer, dict):
        raw_path = str(finalizer.get("trace_path") or "").strip()
        finalizer_path = Path(raw_path) if raw_path else None
        if finalizer_path is not None and finalizer_path.is_file():
            entries.append(("Finalizer", finalizer_path))
    return tuple(entries)


def _mini50_run_roots(project_dir: Path) -> list[Path]:
    """只枚举两条正式 Mini-50 流水线，排除 Smoke 与临时调试 Run。"""

    benchmark_root = project_dir / RUNS_ROOT / "benchmarks"
    roots: list[Path] = []
    for campaign_name in (
        "swebench-verified-mini-50",
        "swebench-verified-mini-50-infrastructure-completion",
    ):
        directory = benchmark_root / campaign_name
        if not directory.is_dir():
            continue
        for campaign_path in directory.glob("*/campaign.json"):
            records, _ = _mini50_records(campaign_path.parent)
            if len(records) == 50:
                roots.append(campaign_path.parent.resolve())
    roots.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return roots


def _mini50_records(run_root: Path) -> tuple[list[dict[str, Any]], str]:
    combined = read_json_file(run_root / "combined_result.json")
    combined_cases = combined.get("cases")
    if isinstance(combined_cases, list):
        records = [item for item in combined_cases if isinstance(item, dict)]
        if records:
            records.sort(
                key=lambda item: int(item.get("ordinal") or item.get("index") or 0)
            )
            return records, str(combined.get("status") or "completed")

    campaign = read_json_file(run_root / "campaign.json")
    campaign_records = campaign.get("records")
    if not isinstance(campaign_records, list):
        return [], str(campaign.get("status") or "not_run")
    records = [item for item in campaign_records if isinstance(item, dict)]
    records.sort(key=lambda item: int(item.get("ordinal") or item.get("index") or 0))
    return records, str(campaign.get("status") or "completed")


def _mini50_trace_entry(
    project_dir: Path,
    record: dict[str, Any],
) -> tuple[str, Path] | None:
    run_dir = _safe_local_path(
        project_dir,
        str(record.get("run_dir") or ""),
        require_directory=True,
    )
    if run_dir is None:
        return None
    direct = run_dir / "trace.json"
    traces = [direct] if direct.is_file() else list(run_dir.glob("cases/**/trace.json"))
    if not traces:
        return None
    trace_path = max(traces, key=lambda path: path.stat().st_mtime)
    case_id = str(record.get("case_id") or record.get("instance_id") or run_dir.name)
    return case_id, trace_path


def _mini50_case_status(record: dict[str, Any]) -> str:
    evidence = record.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    for value in (
        record.get("classification"),
        record.get("official_status"),
        evidence.get("official_evaluation_status"),
        record.get("status"),
    ):
        if str(value or "").strip():
            return str(value)
    return "unknown"


def _display_case_status(status: str) -> str:
    labels = {
        "official_resolved": "Resolved",
        "resolved": "Resolved",
        "official_unresolved": "Unresolved",
        "unresolved": "Unresolved",
        "agent_terminal_empty_patch": "Empty Patch",
        "official_eval_skipped_empty_patch": "Empty Patch",
        "provider_infrastructure": "Provider Infra",
        "runtime_infrastructure": "Runtime Infra",
        "evaluator_infrastructure": "Evaluator Infra",
        "external_interruption": "External Interruption",
    }
    return labels.get(status, status.replace("_", " ").title())


def _first_existing(*candidates: Path) -> Path | None:
    return next((path for path in candidates if path.exists()), None)
