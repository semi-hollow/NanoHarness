import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from agent_forge.multi_agent.adapters.local_worker import LocalAgentWorkerAdapter
from agent_forge.multi_agent.application.live_fanout import LiveFanoutCoordinator
from agent_forge.workbench.adapters.evidence_files import FileEvidenceCatalog
from agent_forge.workbench.application.context_inspection import (
    build_context_turn_inspections,
)
from agent_forge.workbench.presentation.http import (
    INDEX_HTML,
    WORKBENCH_READ_ONLY_MESSAGE,
    _render_evidence_html,
    _render_workspace_view,
)


class WorkbenchRunStoryTest(unittest.TestCase):
    def test_workbench_default_surface_is_read_only(self):
        self.assertIn('class="read-only status-collapsed', INDEX_HTML)
        self.assertIn("一次选择运行，逐层读懂", INDEX_HTML)
        self.assertIn('id="sourceSelect"', INDEX_HTML)
        self.assertIn("选择运行证据", INDEX_HTML)
        self.assertIn("loadEvidence('overview')", INDEX_HTML)
        self.assertIn("loadEvidence('timeline')", INDEX_HTML)
        self.assertIn("loadEvidence('context')", INDEX_HTML)
        self.assertIn("loadEvidence('results')", INDEX_HTML)
        self.assertIn(
            "new URLSearchParams({source: activeSource, view: activeView})",
            INDEX_HTML,
        )
        self.assertNotIn('data-lab="lab1"', INDEX_HTML)
        self.assertNotIn('class="evidence-menu"', INDEX_HTML)
        self.assertIn("pageParams.get('view')", INDEX_HTML)
        self.assertIn("loadEvidence(activeView)", INDEX_HTML)
        self.assertIn("Workbench 只读", WORKBENCH_READ_ONLY_MESSAGE)

    def test_catalog_deduplicates_latest_run_when_it_is_a_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge/runs/governed-run"
            state_dir = project_dir / ".agent_forge/debug-lab/state"
            latest_dir = project_dir / ".agent_forge/latest"
            run_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            latest_dir.mkdir(parents=True)
            (run_dir / "trace.json").write_text(
                json.dumps({"task": "governed repair", "events": []}),
                encoding="utf-8",
            )
            (latest_dir / "run.txt").write_text(str(run_dir), encoding="utf-8")
            (state_dir / "control_artifact.txt").write_text(
                str(run_dir),
                encoding="utf-8",
            )

            sources = FileEvidenceCatalog(project_dir).evidence_sources()

        self.assertEqual(
            [source.key for source in sources],
            ["governed", "orchestration", "complex", "evaluation"],
        )

    def test_all_common_views_render_for_a_single_runtime_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge/runs/runtime-run"
            latest_dir = project_dir / ".agent_forge/latest"
            run_dir.mkdir(parents=True)
            latest_dir.mkdir(parents=True)
            (run_dir / "trace.json").write_text(
                json.dumps(
                    {
                        "task": "repair parser",
                        "status": "completed",
                        "events": [
                            {"step": 1, "event_type": "turn_started"},
                            {"step": 1, "event_type": "llm_call"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "usage.json").write_text(
                json.dumps({"summary": {"llm_calls": 1, "tool_calls": 0}}),
                encoding="utf-8",
            )
            (latest_dir / "run.txt").write_text(str(run_dir), encoding="utf-8")

            rendered = {
                view: _render_workspace_view(
                    project_dir,
                    source_key="latest",
                    view=view,
                )
                for view in ("overview", "timeline", "context", "results")
            }

        self.assertIn("运行摘要", rendered["overview"])
        self.assertIn("执行时间线", rendered["timeline"])
        self.assertIn("上下文与决策", rendered["context"])
        self.assertIn("结果与证据", rendered["results"])

    def test_complex_lab_uses_its_own_pointer_and_explains_real_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            unrelated = project_dir / ".agent_forge/runs/unrelated"
            unrelated.mkdir(parents=True)
            complex_run = project_dir / ".agent_forge/runs/complex-run"
            complex_run.mkdir()
            (complex_run / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "complex-run",
                        "task": "repair settlement atomicity",
                        "status": "completed",
                        "stop_reason": "final_answer",
                        "artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            (complex_run / "trace.json").write_text(
                json.dumps(
                    {
                        "run_id": "complex-run",
                        "stop_reason": "final_answer",
                        "events": [
                            {"step": 1, "event_type": "llm_call"},
                            {
                                "step": 1,
                                "event_type": "validation_evidence",
                                "success": False,
                                "validation": {
                                    "kind": "focused pytest",
                                    "status": "failed",
                                    "evidence": "pytest tests/test_reconciliation.py",
                                },
                            },
                            {"step": 2, "event_type": "llm_call"},
                            {
                                "step": 2,
                                "event_type": "validation_evidence",
                                "success": True,
                                "validation": {
                                    "kind": "full pytest",
                                    "status": "passed",
                                    "evidence": "pytest -q: 8 passed",
                                },
                            },
                            {"step": 2, "event_type": "task_state_checkpoint"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (complex_run / "usage.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "latest_task_status": "completed",
                            "llm_calls": 2,
                            "tool_calls": 7,
                            "failed_tool_calls": 1,
                            "total_tokens": 1234,
                            "estimated_cost_usd": 0.01,
                            "compacted_context_turns": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            (complex_run / "practice_profile.json").write_text(
                json.dumps(
                    {
                        "key": "context-pressure",
                        "title": "上下文压力",
                        "purpose": "观察压缩与信息丢失",
                        "auto_approve_writes": False,
                        "operator_drill": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state = project_dir / ".agent_forge/debug-lab/state"
            state.mkdir(parents=True)
            (state / "complex_artifact.txt").write_text(
                str(complex_run),
                encoding="utf-8",
            )

            catalog = FileEvidenceCatalog(project_dir)
            selected_run = catalog.latest_complex_run_dir()
            rendered = _render_evidence_html(project_dir, "complex")
            context_view = _render_evidence_html(project_dir, "complex_context")
            timeline = _render_evidence_html(project_dir, "complex_timeline")

        self.assertEqual(selected_run, complex_run)
        self.assertIn("repair settlement atomicity", rendered)
        self.assertIn("上下文压力", rendered)
        self.assertIn("逐项人工审批", rendered)
        self.assertIn("2", rendered)
        self.assertIn("focused pytest", rendered)
        self.assertIn("full pytest", rendered)
        self.assertIn("pytest -q: 8 passed", rendered)
        self.assertIn("上下文与决策观察器", context_view)
        self.assertIn("不是隐藏思维链", context_view)
        self.assertIn("Turn 1", context_view)
        self.assertIn("复杂结算修复 AgentLoop", timeline)
        self.assertNotIn(str(unrelated), rendered)

    def test_context_inspector_links_previous_feedback_to_next_turn(self):
        trace = {
            "events": [
                {
                    "step": 1,
                    "event_type": "context_assembly",
                    "context": {
                        "total_chars": 1000,
                        "max_chars": 8000,
                        "available_tools": ["python_validation"],
                        "active_skills": ["bug_fix@1.0.0"],
                        "budget_breakdown": {"system": 200},
                    },
                },
                {
                    "step": 1,
                    "event_type": "context_window",
                    "context_window": {
                        "estimated_tokens_after": 500,
                        "hard_input_limit": 4000,
                        "compacted": False,
                    },
                },
                {
                    "step": 1,
                    "event_type": "model_started",
                    "model_request": {"messages_count": 2},
                },
                {
                    "step": 1,
                    "event_type": "llm_call",
                    "llm_response_summary": "run focused tests",
                    "llm_input_breakdown_chars": {
                        "system_context": 1000,
                        "conversation_history": 200,
                        "tool_schemas": 100,
                    },
                    "model_usage": {"model": "test-model"},
                },
                {
                    "step": 1,
                    "event_type": "action",
                    "tool_call": "python_validation",
                    "tool_arguments": {
                        "validation_target": "tests/test_reconciliation.py"
                    },
                },
                {
                    "step": 1,
                    "event_type": "tool_observation",
                    "success": False,
                    "observation": "exit_code=1\n2 failed",
                },
                {
                    "step": 2,
                    "event_type": "context_assembly",
                    "context": {
                        "total_chars": 1200,
                        "max_chars": 8000,
                        "available_tools": ["replace_text"],
                        "active_skills": ["bug_fix@1.0.0"],
                    },
                },
                {
                    "step": 2,
                    "event_type": "context_window",
                    "context_window": {
                        "estimated_tokens_after": 700,
                        "hard_input_limit": 4000,
                        "compacted": False,
                    },
                },
                {
                    "step": 2,
                    "event_type": "model_started",
                    "model_request": {"messages_count": 4},
                },
                {
                    "step": 2,
                    "event_type": "llm_call",
                    "llm_response_summary": "patch the root cause",
                    "model_usage": {"model": "test-model"},
                },
                {
                    "step": 2,
                    "event_type": "action",
                    "tool_call": "replace_text",
                    "tool_arguments": {"path": "settlement/service.py"},
                },
                {
                    "step": 2,
                    "event_type": "tool_observation",
                    "success": True,
                    "observation": "updated settlement/service.py",
                },
            ]
        }

        turns = build_context_turn_inspections(trace)

        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].phase, "验证失败")
        self.assertIn("2 failed", turns[1].previous_evidence[0])
        self.assertEqual(turns[1].message_delta, 2)
        self.assertTrue(turns[1].tools_changed)
        self.assertEqual(turns[1].phase, "修改代码")

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
                json.dumps(
                    {"task_id": "legacy-only task", "single_status": "completed"}
                ),
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
            worker_a_trace = fanout_run / "workers/a/trace.json"
            worker_b_trace = fanout_run / "workers/b/trace.json"
            finalizer_trace = fanout_run / "finalizer/trace.json"
            for trace_path in (worker_a_trace, worker_b_trace, finalizer_trace):
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                trace_path.write_text(
                    json.dumps(
                        {
                            "run_id": trace_path.parent.name,
                            "stop_reason": "final_answer",
                            "events": [
                                {"step": 1, "event_type": "llm_call"},
                                {"step": 1, "event_type": "final_answer"},
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
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
                            {
                                "task_id": "a",
                                "status": "completed",
                                "trace_path": str(worker_a_trace),
                            },
                            {
                                "task_id": "b",
                                "status": "completed",
                                "trace_path": str(worker_b_trace),
                            },
                        ],
                        "finalizer_trace_path": str(finalizer_trace),
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge/latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(single_run), encoding="utf-8")

            rendered = _render_evidence_html(project_dir, "orchestration")
            timeline = _render_evidence_html(project_dir, "orchestration_timeline")

        self.assertIn("parallel evidence", rendered)
        self.assertIn("这次运行要回答的问题", rendered)
        self.assertIn("两个互不依赖、写入范围不重叠", rendered)
        self.assertIn("本次可复现运行使用确定性 Worker 模型", rendered)
        self.assertIn("为什么允许并行", rendered)
        self.assertIn("a", rendered)
        self.assertIn("b", rendered)
        self.assertIn("查看本次执行过程", rendered)
        self.assertIn("并行多 Agent", timeline)
        self.assertIn("Worker · a", timeline)
        self.assertIn("Worker · b", timeline)
        self.assertIn("Finalizer · 合并后验证", timeline)
        self.assertNotIn("single-run", timeline)

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
            (fanout_dir / "fanout_plan.json").write_text(
                json.dumps(
                    {
                        "goal": "repair pricing and shipping",
                        "tasks": [
                            {
                                "id": "pricing",
                                "task": "fix pricing",
                                "depends_on": [],
                                "write_scope": ["pricing.py"],
                                "allowed_tools": ["replace_text", "git_diff"],
                            },
                            {
                                "id": "shipping",
                                "task": "fix shipping",
                                "depends_on": [],
                                "write_scope": ["shipping.py"],
                                "allowed_tools": ["replace_text", "git_diff"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
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
                                "usage_summary": {
                                    "tool_calls": 1,
                                    "failed_tool_calls": 0,
                                },
                            },
                            {
                                "task_id": "shipping",
                                "status": "completed",
                                "touched_files": ["shipping.py"],
                                "usage_summary": {
                                    "tool_calls": 1,
                                    "failed_tool_calls": 0,
                                },
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
        self.assertIn("任务契约与真实结果", rendered)
        self.assertIn("无前置依赖：允许与同批次任务并行", rendered)
        self.assertIn("replace_text", rendered)
        self.assertIn("失败调用：0 次", rendered)
        self.assertIn("LiveFanoutCoordinator.run", rendered)
        self.assertIn("LiveFanoutCoordinator._mark_dynamic_conflicts", rendered)
        self.assertNotIn("LiveFanoutCoordinator._validate_plan", rendered)
        rendered_entrypoints = set(
            re.findall(
                r"(?:LiveFanoutCoordinator|LocalAgentWorkerAdapter)\.[A-Za-z_][A-Za-z0-9_]*",
                rendered,
            )
        )
        self.assertEqual(
            rendered_entrypoints,
            {
                "LiveFanoutCoordinator.run",
                "LiveFanoutCoordinator._run_batch",
                "LiveFanoutCoordinator._mark_dynamic_conflicts",
                "LiveFanoutCoordinator._merge_batch",
                "LocalAgentWorkerAdapter.run_finalizer",
            },
        )
        self.assertNotIn("最新运行没有标准运行清单", rendered)

    def test_workbench_fanout_entrypoints_exist_in_code(self):
        entrypoints = (
            (LiveFanoutCoordinator, "run"),
            (LiveFanoutCoordinator, "_run_batch"),
            (LiveFanoutCoordinator, "_mark_dynamic_conflicts"),
            (LiveFanoutCoordinator, "_merge_batch"),
            (LocalAgentWorkerAdapter, "run_finalizer"),
        )

        for owner, method_name in entrypoints:
            with self.subTest(entrypoint=f"{owner.__name__}.{method_name}"):
                self.assertTrue(hasattr(owner, method_name))

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
        self.assertIn("写操作需要人工审批时", rendered)
        self.assertIn("Operation Ledger 与目标 Fingerprint", rendered)
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

    def test_timeline_explains_each_checkpoint_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge/runs/control-run"
            run_dir.mkdir(parents=True)
            trace_path = run_dir / "trace.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "run_id": "control-run",
                        "events": [
                            {
                                "step": 0,
                                "event_type": "task_state_checkpoint",
                                "task_state": {
                                    "status": "created",
                                    "current_step": 0,
                                    "messages_count": 0,
                                    "observations_count": 0,
                                },
                            },
                            {
                                "step": 1,
                                "event_type": "task_state_checkpoint",
                                "task_state": {
                                    "status": "running",
                                    "current_step": 1,
                                    "messages_count": 1,
                                    "observations_count": 0,
                                },
                            },
                            {
                                "step": 1,
                                "event_type": "task_state_checkpoint",
                                "task_state": {
                                    "status": "waiting_approval",
                                    "current_step": 1,
                                    "last_tool": "replace_text",
                                    "messages_count": 1,
                                    "observations_count": 0,
                                },
                            },
                            {
                                "step": 1,
                                "event_type": "task_state_checkpoint",
                                "task_state": {
                                    "status": "running",
                                    "current_step": 1,
                                    "last_tool": "replace_text",
                                    "messages_count": 1,
                                    "observations_count": 0,
                                },
                            },
                            {
                                "step": 1,
                                "event_type": "task_state_checkpoint",
                                "task_state": {
                                    "status": "running",
                                    "current_step": 1,
                                    "last_tool": "replace_text",
                                    "last_observation": "changed target.py",
                                    "messages_count": 2,
                                    "observations_count": 1,
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state_dir = project_dir / ".agent_forge/debug-lab/state"
            state_dir.mkdir(parents=True)
            (state_dir / "control_artifact.txt").write_text(
                str(run_dir),
                encoding="utf-8",
            )

            rendered = _render_evidence_html(project_dir, "timeline")

        self.assertIn("记录第 1 轮起点", rendered)
        self.assertIn("进入审批等待", rendered)
        self.assertIn("审批后恢复运行", rendered)
        self.assertIn("保存工具结果", rendered)
        self.assertIn("副作用执行前形成可恢复人工屏障", rendered)
        self.assertIn("恢复时不重复执行", rendered)

    def test_timeline_does_not_present_rejected_tool_request_as_final_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge/runs/governed-run"
            run_dir.mkdir(parents=True)
            (run_dir / "trace.json").write_text(
                json.dumps(
                    {
                        "run_id": "blocked-run",
                        "stop_reason": "pending_tool_call_at_stop",
                        "events": [
                            {
                                "step": 1,
                                "event_type": "llm_call",
                                "llm_response_summary": (
                                    '<tool_calls><invoke name="read_file">'
                                ),
                            },
                            {
                                "step": 1,
                                "event_type": "final_answer",
                                "success": False,
                                "pending_tool_call": True,
                            },
                            {
                                "step": 1,
                                "event_type": "run_completed",
                                "run_status": "blocked",
                                "stop_reason": "pending_tool_call_at_stop",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state_dir = project_dir / ".agent_forge/debug-lab/state"
            state_dir.mkdir(parents=True)
            (state_dir / "control_artifact.txt").write_text(
                str(run_dir),
                encoding="utf-8",
            )

            rendered = _render_evidence_html(project_dir, "timeline")

        self.assertIn("收口失败：仍请求 read_file，未执行", rendered)
        self.assertIn("收口失败：工具请求未执行", rendered)
        self.assertIn("read_file · 未进入工具执行链", rendered)
        self.assertNotIn("第 1 轮 · 形成最终回答", rendered)

    def test_evaluation_page_declares_independent_swebench_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            benchmark_run = project_dir / ".agent_forge/runs/swebench-run"
            benchmark_run.mkdir(parents=True)
            trace_path = benchmark_run / "trace.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "events": [
                            {"event_type": "action", "tool_call": "read_file"},
                            {"event_type": "action", "tool_call": "grep_search"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (benchmark_run / "results.json").write_text(
                json.dumps(
                    {
                        "case_results": [
                            {
                                "instance_id": "demo__case-1",
                                "status": "blocked",
                                "evaluation_status": "official_eval_skipped_empty_patch",
                                "failure_class": "pending_tool_call_at_stop",
                                "patch_chars": 0,
                                "trace_path": str(trace_path),
                                "diagnosis": (
                                    "The model still requested a tool on the final turn, "
                                    "so the runtime blocked an incomplete artifact."
                                ),
                                "next_actions": [
                                    "Inspect the final model action and increase budget or force "
                                    "an earlier patch/no-patch decision."
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            latest_dir = project_dir / ".agent_forge/latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "bench.txt").write_text(
                str(benchmark_run),
                encoding="utf-8",
            )
            rendered = _render_evidence_html(project_dir, "evaluation")

        self.assertIn("独立证据 · SWE-BENCH CASE", rendered)
        self.assertIn("demo__case-1", rendered)
        self.assertIn("当前结论只属于评测运行 swebench-run", rendered)
        self.assertIn("Worker、Finalizer 和协调结果属于另一条运行", rendered)
        self.assertIn(str(benchmark_run / "results.json"), rendered)
        self.assertIn("0 字符（只检索，未写入）", rendered)
        self.assertIn("2 次工具调用均未进入写操作", rendered)
        self.assertIn("本次未运行", rendered)
        self.assertNotIn("未运行 字符", rendered)
        self.assertIn(
            "模型在最后一轮仍请求调用工具，因此运行时阻断了不完整产物", rendered
        )
        self.assertIn("增加步骤预算，或要求模型更早明确", rendered)
        self.assertIn("结果与证据", INDEX_HTML)

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
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "config": {
                            "regression_set": "verified-commissioning-2",
                            "case_ids": [
                                "astropy__astropy-12907",
                                "django__django-11133",
                            ],
                            "repetitions": 1,
                            "benchmark": {"model": "deepseek-v4-pro"},
                            "variants": [
                                {"name": "minimal-control"},
                                {"name": "governed-runtime"},
                            ],
                        },
                        "records": [
                            {
                                "case_id": "astropy__astropy-12907",
                                "variant": "minimal-control",
                                "repetition": 1,
                                "status": "completed",
                            },
                            {
                                "case_id": "astropy__astropy-12907",
                                "variant": "governed-runtime",
                                "repetition": 1,
                                "status": "completed",
                            },
                            {
                                "case_id": "django__django-11133",
                                "variant": "minimal-control",
                                "repetition": 1,
                                "status": "completed",
                            },
                            {
                                "case_id": "django__django-11133",
                                "variant": "governed-runtime",
                                "repetition": 1,
                                "status": "completed",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "summary.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "status": "completed",
                        "planned_runs": 4,
                        "status_counts": {"completed": 4},
                        "paired_official": {"evaluated_pairs": 2},
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
                            "finding": "Failures came from validation environments, not edit-tool crashes.",
                            "evidence": [
                                "Control: 8/27; six environment failures and two policy denials.",
                                "Treatment: 5/32; four environment failures and one invalid target.",
                            ],
                        },
                        "hypothesis": "routing reduces failed calls",
                        "change": {"reference": "governed preset"},
                        "regression_cases": ["case-a", "case-b"],
                        "before_after": {
                            "control": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "tool_calls": 27,
                                "failed_tool_calls": 8,
                                "total_tokens": 100,
                                "estimated_cost_usd": 0.1,
                            },
                            "treatment": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "tool_calls": 32,
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
            benchmark = _render_evidence_html(project_dir, "benchmark")

        self.assertIn("这次运行要回答的问题", rendered)
        self.assertIn("本次载入的历史实验", rendered)
        self.assertIn("嵌套 CompoundModel 的可分离矩阵错误", rendered)
        self.assertIn("HttpResponse 错误处理 memoryview", rendered)
        self.assertIn("打开评测档案不会重新调用模型", rendered)
        self.assertIn("deepseek-v4-pro", rendered)
        self.assertIn("观测问题", rendered)
        self.assertIn("维护者人工复核", rendered)
        self.assertIn("已人工复核", rendered)
        self.assertIn("失败工具调用差值", rendered)
        self.assertIn("5 - 8 = -3", rendered)
        self.assertIn("8 / 27（29.6%）", rendered)
        self.assertIn("5 / 32（15.6%）", rendered)
        self.assertIn("six environment failures and two policy denials", rendered)
        self.assertIn("+30（多用 30 Token）", rendered)
        self.assertIn("差值都按“治理增强版 - 基础控制版”计算", rendered)
        self.assertIn("继续迭代", rendered)
        self.assertIn("仅限试运行证据", rendered)
        self.assertNotIn("Failed Tool Delta", rendered)
        self.assertNotIn("negative is fewer failures", rendered)
        self.assertIn("当前 Case 与重复实验结果", benchmark)
        self.assertIn("Case 任务摘要", benchmark)
        self.assertIn("真实运行输入是 SWE-bench 的 problem_statement", benchmark)
        self.assertIn("commissioning 子集", benchmark)
        self.assertNotIn("Smoke-5 是面向 Harness 机制", benchmark)


if __name__ == "__main__":
    unittest.main()
