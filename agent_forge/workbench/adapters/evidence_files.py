from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.campaign import CampaignState, summarize_campaign
from agent_forge.observability.api import RunStory, load_run_story
from agent_forge.workbench.domain import EvidenceSource
from agent_forge.workbench.ports import EvidenceCatalogPort


class FileEvidenceCatalog(EvidenceCatalogPort):

    def __init__(self, project_dir: Path) -> None:

        self.project_dir = project_dir.absolute()

    # 主要入口：把不同目录布局的运行统一成 Workbench 可选择的证据来源。
    def evidence_sources(self) -> tuple[EvidenceSource, ...]:
        """返回稳定的运行选择列表，并消除同一 artifact 的重复入口。

        三个预置场景和评测批次即使尚未运行也保留，帮助使用者发现入口。普通
        ``Harness.run`` 只有在不属于这些预置场景时才以“最近运行”出现；否则同一份
        证据会在下拉框里出现两次，反而让人误以为它们是两次不同实验。
        """

        governed = self._governed_source()
        orchestration = self._orchestration_source()
        complex_repair = self._complex_source()
        evaluation = self._benchmark_source()
        preset_sources = (governed, orchestration, complex_repair, evaluation)
        latest = self._latest_runtime_source()

        if latest.available and not any(
            self._same_run(latest, source) for source in preset_sources
        ):
            return (latest, *preset_sources)
        return preset_sources

    @staticmethod
    def _same_run(left: EvidenceSource, right: EvidenceSource) -> bool:
        """判断两个选择项是否最终指向同一个不可变 Run 目录。"""

        if left.run_dir is None or right.run_dir is None:
            return False
        return left.run_dir.resolve() == right.run_dir.resolve()

    def _latest_runtime_source(self) -> EvidenceSource:
        """把 ``run.txt`` 指向的任意运行接入公共视图，而非只支持预置场景。"""

        pointer = self.project_dir / ".agent_forge/latest/run.txt"
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
                usage_path
                if usage_path is not None and usage_path.is_file()
                else None
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

    def _complex_source(self) -> EvidenceSource:
        run_dir = self.latest_complex_run_dir()
        trace_path = self.latest_complex_trace_path()
        story = self._load_story_if_present(run_dir)
        trace = read_json_file(trace_path)
        return EvidenceSource(
            key="complex",
            title="复杂真实修复",
            description="多轮检索、修改、验证失败、人工控制与最终收敛",
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
            trace_entries=(("复杂修复 AgentLoop", trace_path),) if trace_path else (),
            usage_path=self.latest_complex_usage_path(),
        )

    def _benchmark_source(self) -> EvidenceSource:
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
        return EvidenceSource(
            key="evaluation",
            title=title,
            description="固定样本、配对运行、官方结果、成本与失败分布",
            source_type="benchmark",
            task=task,
            status=status,
            primary_path=campaign_dir,
            run_dir=benchmark_run,
            trace_entries=tuple(trace_entries),
            usage_path=self.latest_benchmark_usage_path(),
        )

    @staticmethod
    def _load_story_if_present(run_dir: Path | None) -> RunStory | None:
        if run_dir is None or not (run_dir / "run_manifest.json").is_file():
            return None
        try:
            return load_run_story(run_dir)
        except (OSError, ValueError):
            return None

    def latest_run_dir(self) -> Path | None:
        latest = self.project_dir / ".agent_forge/latest"
        runs_dir = self.project_dir / ".agent_forge/runs"
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

        pointer = self.project_dir / ".agent_forge/debug-lab/state/control_artifact.txt"
        run_dir = self._run_dir_from_pointer(pointer)
        runs_dir = self.project_dir / ".agent_forge/runs"
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

    def latest_complex_run_dir(self) -> Path | None:
        """返回复杂真实修复场景，绝不回退到其他运行或 Benchmark。"""

        pointer = self.project_dir / ".agent_forge/debug-lab/state/complex_artifact.txt"
        run_dir = self._run_dir_from_pointer(pointer)
        runs_dir = self.project_dir / ".agent_forge/runs"
        if run_dir is not None and _is_under(run_dir, runs_dir):
            return run_dir
        return None

    def latest_complex_run_story(self) -> RunStory | None:
        """加载复杂真实任务的标准 Run Story。"""

        run_dir = self.latest_complex_run_dir()
        if run_dir is None or not (run_dir / "run_manifest.json").is_file():
            return None
        return load_run_story(run_dir)

    def latest_complex_trace_path(self) -> Path | None:
        """返回复杂真实任务的 Trace。"""

        return _latest_artifact_in_run(
            self.latest_complex_run_dir(),
            direct_name="trace.json",
            nested_pattern="cases/**/trace.json",
        )

    def latest_complex_usage_path(self) -> Path | None:
        """返回复杂真实任务的 Usage 投影。"""

        return _latest_artifact_in_run(
            self.latest_complex_run_dir(),
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

        runs_dir = self.project_dir / ".agent_forge/runs"
        candidates: list[Path] = []
        current = self.latest_multi_agent_summary_path()
        if current is not None:
            candidates.append(current)
        if runs_dir.exists():
            candidates.extend(runs_dir.glob("**/multi_agent_summary.json"))
        return _newest_existing(candidates)

    def latest_orchestration_fanout_path(self) -> Path | None:
        """返回最近一次并行 Fanout 证据，不受其他 Lab 的运行顺序影响。"""

        runs_dir = self.project_dir / ".agent_forge/runs"
        candidates: list[Path] = []
        current = self.latest_fanout_summary_path()
        if current is not None:
            candidates.append(current)
        if runs_dir.exists():
            candidates.extend(runs_dir.glob("**/fanout_summary.json"))
        return _newest_existing(candidates)

    def latest_benchmark_run_dir(self) -> Path | None:
        """返回最近一次 SWE-bench 运行，和交互式 Single-Run 分开选取。"""

        latest = self.project_dir / ".agent_forge/latest/bench.txt"
        runs_dir = self.project_dir / ".agent_forge/runs"
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

    def latest_direct_baseline_record(self) -> dict[str, Any]:
        run_dir = self.latest_benchmark_run_dir()
        path = (
            run_dir / "direct_baseline_predictions.jsonl"
            if run_dir is not None
            else None
        )
        if path is None or not path.exists():
            return {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                return record
        return {}

    def latest_campaign_dir(self) -> Path | None:
        """优先返回显式发布的 Campaign，避免工作台悄悄切换实验批次。"""

        latest = self.project_dir / ".agent_forge/latest/campaign.txt"
        campaigns = self.project_dir / ".agent_forge/campaigns"
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
