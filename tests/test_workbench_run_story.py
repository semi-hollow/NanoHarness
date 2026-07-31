import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_forge.workbench.adapters.evidence_files import FileEvidenceCatalog
from agent_forge.workbench.presentation.http import (
    INDEX_HTML,
    WORKBENCH_READ_ONLY_MESSAGE,
    _render_evidence_html,
)


class WorkbenchRunStoryTest(unittest.TestCase):
    def test_workbench_default_surface_is_read_only(self):
        self.assertIn('class="read-only status-collapsed', INDEX_HTML)
        self.assertIn("总览 + 三个证据场景", INDEX_HTML)
        self.assertIn("loadEvidence('overview')", INDEX_HTML)
        self.assertIn("1 受治理运行", INDEX_HTML)
        self.assertIn("2 多 Agent 协同", INDEX_HTML)
        self.assertIn("3 评测改进闭环", INDEX_HTML)
        self.assertIn("loadEvidence('controls')", INDEX_HTML)
        self.assertIn("pageParams.get('view')", INDEX_HTML)
        self.assertIn("loadEvidence(initialView)", INDEX_HTML)
        self.assertIn("Workbench 只读", WORKBENCH_READ_ONLY_MESSAGE)

    def test_run_evidence_prefers_canonical_run_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge" / "runs" / "run-canonical"
            run_dir.mkdir(parents=True)
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run-canonical",
                        "task": "canonical task",
                        "status": "completed",
                        "stop_reason": "final_answer",
                        "artifacts": [
                            {
                                "artifact_id": "patch",
                                "kind": "candidate_diff",
                                "relative_path": "candidate_changes.diff",
                                "producer_symbol": "ExecutionEnvironment.diff",
                                "flow_stage": "artifacts",
                                "semantic_consumers": ["local evaluator"],
                                "evidence_level": "candidate",
                                "proves": ["a candidate patch was produced"],
                                "does_not_prove": ["official benchmark resolution"],
                                "byte_size": 18,
                            },
                            {
                                "artifact_id": "local-report",
                                "kind": "local_report",
                                "relative_path": "local_report.json",
                                "producer_symbol": "LocalEvaluator.evaluate",
                                "flow_stage": "evidence",
                                "evidence_level": "local",
                                "proves": ["local checks were recorded"],
                                "does_not_prove": ["official benchmark resolution"],
                                "byte_size": 42,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.json").write_text(
                json.dumps(
                    {
                        "events": [
                            {"event_type": "turn_started"},
                            {"event_type": "tool_call"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "comparison.json").write_text(
                json.dumps({"task_id": "stale legacy task", "single_status": "failed"}),
                encoding="utf-8",
            )

            story = FileEvidenceCatalog(project_dir).latest_run_story()
            rendered = _render_evidence_html(project_dir, "evidence")

        self.assertIsNotNone(story)
        self.assertEqual(story.run_id, "run-canonical")
        self.assertIn("运行全链路", rendered)
        self.assertIn("主链阶段", rendered)
        self.assertIn("默认隐藏模块名和事件计数", rendered)
        self.assertIn("run_manifest.json", rendered)
        self.assertIn("canonical task", rendered)
        self.assertNotIn("stale legacy task", rendered)
        self.assertIn("ToolExecutionPipeline.execute_calls", rendered)
        self.assertIn("candidate_changes.diff", rendered)
        self.assertIn("候选结果", rendered)
        self.assertIn("本地验证", rendered)
        self.assertIn("官方评测", rendered)
        self.assertIn("官方基准评测已解决", rendered)
        self.assertIn("Trace 记录，不是", rendered)

    def test_run_evidence_keeps_legacy_fallback_without_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge" / "runs" / "run-legacy"
            run_dir.mkdir(parents=True)
            (run_dir / "comparison.json").write_text(
                json.dumps({"task_id": "legacy-only task", "single_status": "completed"}),
                encoding="utf-8",
            )

            catalog = FileEvidenceCatalog(project_dir)
            story = catalog.latest_run_story()
            rendered = _render_evidence_html(project_dir, "evidence")

        self.assertIsNone(story)
        self.assertIn("没有标准运行清单", rendered)
        self.assertIn("兼容旧格式", rendered)
        self.assertIn("legacy-only task", rendered)
        self.assertIn("运行全链路", rendered)
        self.assertIn("查看本次触发的上下文、记忆、Skill 与工具适配信号", rendered)

    def test_explicit_latest_run_pointer_wins_over_stale_directory_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            runs = project_dir / ".agent_forge" / "runs"
            stale = runs / "stale"
            current = runs / "control" / "phases" / "run-current"
            stale.mkdir(parents=True)
            current.mkdir(parents=True)
            os.utime(stale, (4_000_000_000, 4_000_000_000))
            latest = project_dir / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(current), encoding="utf-8")

            selected = FileEvidenceCatalog(project_dir).latest_run_dir()

        self.assertEqual(selected, current)

    def test_orchestration_view_keeps_latest_fanout_after_single_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            runs = project_dir / ".agent_forge/runs"
            single_run = runs / "single-run"
            fanout_run = runs / "fanout-run/fanout"
            single_run.mkdir(parents=True)
            fanout_run.mkdir(parents=True)
            (single_run / "trace.json").write_text('{"events": []}', encoding="utf-8")
            (fanout_run / "fanout_summary.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "goal": "parallel evidence",
                        "batches": [["a", "b"]],
                        "metrics": {
                            "task_count": 2,
                            "completed_count": 2,
                            "max_workers": 2,
                        },
                        "results": [
                            {"task_id": "a", "status": "completed"},
                            {"task_id": "b", "status": "completed"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge/latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(single_run), encoding="utf-8")

            rendered = _render_evidence_html(project_dir, "orchestration")

        self.assertIn("parallel evidence", rendered)
        self.assertIn("为什么允许并行", rendered)
        self.assertIn("a", rendered)
        self.assertIn("b", rendered)

    def test_run_evidence_aggregates_fanout_metrics_and_full_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge/runs/fanout-run"
            fanout_dir = run_dir / "fanout"
            finalizer_dir = fanout_dir / "finalizer"
            finalizer_dir.mkdir(parents=True)
            for path in (
                fanout_dir / "fanout_plan.json",
                fanout_dir / "fanout_checkpoint.json",
                fanout_dir / "integrated_changes.diff",
                fanout_dir / "fanout_report.md",
                finalizer_dir / "trace.json",
                finalizer_dir / "usage.json",
                finalizer_dir / "verification.md",
            ):
                path.write_text("evidence", encoding="utf-8")
            summary_path = fanout_dir / "fanout_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "run_id": "fanout-1",
                        "goal": "repair pricing and shipping",
                        "status": "passed",
                        "batches": [["pricing", "shipping"]],
                        "results": [
                            {
                                "task_id": "pricing",
                                "status": "completed",
                                "touched_files": ["pricing.py"],
                                "usage_summary": {"tool_calls": 1},
                            },
                            {
                                "task_id": "shipping",
                                "status": "completed",
                                "touched_files": ["shipping.py"],
                                "usage_summary": {"tool_calls": 1},
                            },
                        ],
                        "merged_task_ids": ["pricing", "shipping"],
                        "conflicts": [],
                        "metrics": {
                            "llm_calls": 6,
                            "tool_calls": 3,
                            "total_tokens": 0,
                        },
                        "final_decision": "PASS",
                        "finalizer_trace_path": str(finalizer_dir / "trace.json"),
                        "finalizer_usage_path": str(finalizer_dir / "usage.json"),
                        "finalizer_usage_summary": {"tool_calls": 1},
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge/latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")

            rendered = _render_evidence_html(project_dir, "evidence")

        self.assertIn("多 Agent 运行证据", rendered)
        self.assertIn("模型调用（确定性）", rendered)
        self.assertIn("Worker 2 次 + Finalizer 1 次", rendered)
        self.assertIn("计划与依赖检查", rendered)
        self.assertIn("Worker 隔离执行", rendered)
        self.assertIn("候选改动合并", rendered)
        self.assertIn("隔离 Finalizer", rendered)
        self.assertIn("未调用外部大模型", rendered)
        self.assertNotIn("最新运行没有标准运行清单", rendered)

    def test_governed_view_keeps_lab1_evidence_after_lab2_updates_latest_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            runs = project_dir / ".agent_forge/runs"
            control_run = runs / "approval-run/phases/control-run"
            fanout_run = runs / "fanout-run"
            control_run.mkdir(parents=True)
            fanout_run.mkdir(parents=True)
            control_trace = control_run / "trace.json"
            control_trace.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "event_type": "task_state_checkpoint",
                                "step": 2,
                                "task_state": {
                                    "metadata": {
                                        "execution_environment": {
                                            "mode": "worktree",
                                            "network_policy": "deny",
                                        }
                                    }
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge/latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(fanout_run), encoding="utf-8")
            state = project_dir / ".agent_forge/debug-lab/state"
            state.mkdir(parents=True)
            (state / "control_artifact.txt").write_text(
                str(control_run),
                encoding="utf-8",
            )

            rendered = _render_evidence_html(project_dir, "controls")
            overview = _render_evidence_html(project_dir, "overview")
            timeline = _render_evidence_html(project_dir, "timeline")

        self.assertIn("Runtime 控制面", rendered)
        self.assertIn(str(control_trace), rendered)
        self.assertIn("1 个 Checkpoint", overview)
        self.assertIn("受治理 AgentLoop", timeline)
        self.assertIn(str(control_trace), timeline)

    def test_overview_exposes_progressive_evidence_hierarchy(self):
        with tempfile.TemporaryDirectory() as tmp:
            rendered = _render_evidence_html(Path(tmp), "overview")

        self.assertIn("先看结论，再逐层下钻", rendered)
        self.assertIn("实验批次", rendered)
        self.assertIn("单次运行", rendered)
        self.assertIn("Agent 轮次", rendered)
        self.assertIn("语义阶段", rendered)
        self.assertIn("原始事件", rendered)

    def test_published_campaign_bundle_uses_manifest_and_summary_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            campaign = project_dir / "published-campaign"
            campaign.mkdir()
            (campaign / "manifest.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "status": "completed",
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "summary.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "status": "completed",
                        "variants": {},
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "campaign.txt").write_text(str(campaign), encoding="utf-8")

            catalog = FileEvidenceCatalog(project_dir)

            self.assertEqual(
                catalog.latest_campaign_state()["campaign_id"],
                "campaign-1",
            )
            self.assertEqual(
                catalog.latest_campaign_summary()["status"],
                "completed",
            )

    def test_improvement_view_renders_reviewed_before_after_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            campaign = project_dir / "campaign"
            campaign.mkdir()
            (campaign / "manifest.json").write_text(
                json.dumps({"campaign_id": "campaign-1", "records": []}),
                encoding="utf-8",
            )
            (campaign / "summary.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "status": "completed",
                        "variants": {},
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "improvement_record.json").write_text(
                json.dumps(
                    {
                        "observed_problem": "failed tool calls were noisy",
                        "diagnosis": {
                            "source": "maintainer_review",
                            "review_status": "reviewed",
                        },
                        "hypothesis": "routing reduces failed calls",
                        "change": {"reference": "governed preset"},
                        "regression_cases": ["case-a", "case-b"],
                        "before_after": {
                            "control": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "failed_tool_calls": 8,
                                "total_tokens": 100,
                                "estimated_cost_usd": 0.1,
                            },
                            "treatment": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "failed_tool_calls": 5,
                                "total_tokens": 130,
                                "estimated_cost_usd": 0.13,
                            },
                            "delta": {
                                "official_resolved": 0,
                                "failed_tool_calls": -3,
                                "total_tokens": 30,
                                "estimated_cost_usd": 0.03,
                            },
                        },
                        "decision": {
                            "status": "iterate",
                            "rationale": "correctness tied and cost increased",
                        },
                        "claim_boundary": "commissioning evidence only",
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "campaign.txt").write_text(str(campaign), encoding="utf-8")

            rendered = _render_evidence_html(project_dir, "feedback")

        self.assertIn("观测问题", rendered)
        self.assertIn("维护者人工复核", rendered)
        self.assertIn("已人工复核", rendered)
        self.assertIn("失败工具调用差值", rendered)
        self.assertIn("-3（少 3 次失败）", rendered)
        self.assertIn("+30（多用 30 Token）", rendered)
        self.assertIn("差值都按“治理增强版 - 基础控制版”计算", rendered)
        self.assertIn("继续迭代", rendered)
        self.assertIn("仅限试运行证据", rendered)
        self.assertNotIn("Failed Tool Delta", rendered)
        self.assertNotIn("negative is fewer failures", rendered)


if __name__ == "__main__":
    unittest.main()
