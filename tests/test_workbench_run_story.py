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
    _canonical_score_is_publishable,
    _render_evidence_html,
    _render_workspace_view,
    _tone_for_status,
)


def _phase2_runtime_quality_summary() -> dict[str, object]:
    """用于 Workbench 兼容测试的 schema v3 最小发布摘要。"""

    phase1_metrics = {
        "planned": 10,
        "official_resolved": 5,
        "official_unresolved": 0,
        "official_empty_or_skipped": 5,
        "official_infrastructure_error": 0,
        "official_decided": 5,
        "provider_tokens": 1000,
        "estimated_cost_usd": 0.01,
    }
    return {
        "schema_version": 3,
        "experiment_type": "runtime_quality",
        "title": "Runtime 质量实验",
        "status": "completed",
        "question": "Runtime 如何从可审计失败中改进？",
        "case_ids": ["demo__phase1"],
        "reference_iteration": "R0",
        "accepted_iteration": None,
        "reference_metrics": phase1_metrics,
        "iterations": [
            {
                "id": "R0",
                "cohort": "Phase-1 Golden-10",
                "decision": "reference",
                "metrics": phase1_metrics,
                "run_dirs": [".agent_forge/phase1/run"],
            }
        ],
        "case_results": [],
        "boundaries": ["Phase 1 固定小样本，不外推。"],
        "phase2": {
            "id": "operation-ledger-restored-precondition",
            "title": "Phase 2 · Operation Ledger 恢复前置条件重放",
            "status": "completed",
            "decision": "accepted",
            "question": "恢复到前置状态后，受限重放能否保留正确 Patch？",
            "claim_scope": (
                "post-hoc Case-level 机制故事；不外推为 "
                "SWE-bench Verified 总体解决率提升"
            ),
            "reference": {
                "id": "P2-R0",
                "model": "opencode-go/glm-5.2",
                "metrics": {
                    "planned": 10,
                    "official_resolved": 5,
                },
            },
            "treatment": {
                "commit": "treatment-demo",
                "mechanism_marker": "replay_authorized_restored_precondition",
            },
            "case_study": {
                "baseline_metrics": {
                    "planned": 2,
                    "official_resolved": 0,
                },
                "treatment_metrics": {
                    "planned": 2,
                    "official_resolved": 2,
                    "mechanism_activation_observed": 2,
                    "mechanism_activation_expected": 2,
                },
            },
            "guards": {
                "metrics": {
                    "planned": 3,
                    "official_resolved": 3,
                    "unsafe_replay_count": 0,
                }
            },
            "golden10_expansion": {
                "status": "completed",
                "baseline_metrics": {
                    "planned": 10,
                    "official_resolved": 5,
                },
                "treatment_metrics": {
                    "planned": 10,
                    "official_resolved": 6,
                },
                "net_official_resolved_delta": 1,
                "baseline_resolved_regressions": [],
                "case_results": [
                    {
                        "case_id": "demo__golden",
                        "baseline_status": "official_empty_or_skipped",
                        "treatment_status": "official_resolved",
                        "transition": "empty_to_resolved",
                    }
                ],
                "run_dirs": [".agent_forge/phase2/golden/run"],
            },
            "gates": {
                "target_outcome": {
                    "status": "passed",
                    "evidence": "0/2 → 2/2",
                },
                "correctness_guards": {
                    "status": "passed",
                    "evidence": "3/3 保持 resolved",
                },
                "golden10_non_regression": {
                    "status": "passed",
                    "evidence": "6/10，原 resolved 无回归",
                },
            },
            "usage": {
                "target_and_guards_total": {
                    "total_tokens": 777258,
                    "llm_calls": 86,
                    "estimated_cost_usd": 0.905107,
                },
                "golden10_expansion": {
                    "total_tokens": 2490765,
                    "llm_calls": 205,
                    "estimated_cost_usd": 3.224585,
                },
                "phase2_case_study_and_expansion_total": {
                    "total_tokens": 3268023,
                    "llm_calls": 291,
                    "estimated_cost_usd": 4.129692,
                },
            },
            "evidence_run_dirs": {
                "target": [".agent_forge/phase2/target/run"],
                "guards": [".agent_forge/phase2/guards/run"],
                "golden10_expansion": [".agent_forge/phase2/golden/run"],
            },
            "supported_claims": ["Target 个案中受限重放与 Patch 保留同时出现。"],
            "unsupported_claims": ["Operation Ledger 是唯一因果。"],
            "boundaries": ["Target 为 post-hoc 选择的 Case-level 证据。"],
        },
    }


def _canonical_showcase_summary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": "canonical_showcase",
        "showcase_id": "canonical-showcase-v1",
        "title": "NanoHarness · Canonical Showcase",
        "status": "pre_registration_in_progress",
        "current_profile": {
            "profile_id": "showcase-quality-v1",
            "status": "candidate_comparison_pending",
            "frozen": False,
            "selected_model": None,
            "model_candidates": [
                "opencode-go/deepseek-v4-pro",
                "opencode-go/glm-5.2",
            ],
            "references": {
                "selection_set": "Golden-10",
                "selection_role": "development_and_regression_only",
            },
        },
        "canonical_evaluation": {
            "evaluation_id": "canonical-50-v1",
            "status": "not_started",
            "dataset": "princeton-nlp/SWE-bench_Verified",
            "protocol": "Pass@1",
            "cohort_frozen": False,
            "protocol_frozen": False,
            "planned": 50,
            "completed": None,
            "terminal_accounted": None,
            "official_evaluated": None,
            "empty_patch": None,
            "provider_infra": None,
            "evaluator_infra": None,
            "official_resolved": None,
            "evidence_validated": False,
            "claim": (
                "Result applies only to this deterministic 50-case sample, "
                "not the full SWE-bench Verified benchmark."
            ),
        },
        "supporting_checks": [
            {
                "id": "golden-10",
                "label": "Golden-10",
                "role": "development_and_regression_only",
                "quality_headline": False,
                "status": "candidate_comparison_pending",
            },
            {
                "id": "infrastructure-smoke-5",
                "label": "Infrastructure Smoke-5",
                "role": "infrastructure_health_only",
                "quality_headline": False,
                "status": "available",
            },
        ],
        "boundaries": ["No score before complete official adjudication."],
    }


def _quality_selection_incident_summary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": "quality_selection_incident",
        "title": "Quality Selection v1 · Fail-Closed Incident",
        "status": "invalid_no_winner",
        "question": "Why did selection produce no winner?",
        "headline_eligible": False,
        "incident": {
            "planned_case_starts": 20,
            "slots_before_uniformly_contaminated_tail": 14,
            "rate_limit_free_case_slots": 11,
            "rate_limit_affected_case_slots": 9,
            "uniformly_contaminated_tail_slots": 6,
        },
        "decision": {
            "summarizer_exit_code": 2,
            "winner": None,
            "correctness_reruns_performed": 0,
        },
        "source_binding": {
            "source_tag": "quality-selection-test-tag",
            "source_commit_sha": "abc123",
            "protocol_sha256": "protocol-sha",
            "command_manifest_sha256": "manifest-sha",
        },
        "run_artifacts": [
            {
                "candidate_id": "v4-pro",
                "shard": "shard-c",
                "slot_range": [15, 17],
                "identity": {
                    "provider": "opencode-go",
                    "requested_model": "deepseek-v4-pro",
                    "provider_reported_models": [],
                },
                "usage": {"llm_calls": 3, "total_tokens": 100},
                "stop_reasons": {"invalid_llm_response": 3},
                "rate_limit": {"affected_cases": 3},
                "official_aggregate_safe_counts": {
                    "total_instances": 3,
                    "completed_instances": 0,
                    "empty_patch_instances": 3,
                    "error_instances": 0,
                },
            }
        ],
        "claim_boundary": ["No winner may be selected from the partial prefix."],
    }


class WorkbenchRunStoryTest(unittest.TestCase):
    def test_published_quality_showcase_renders_current_40_percent_observation(self):
        project_dir = Path(__file__).parents[1]

        results = _render_workspace_view(
            project_dir,
            source_key="evaluation",
            view="results",
        )

        self.assertIn("QUALITY SHOWCASE", results)
        self.assertIn("固定开发样本", results)
        self.assertIn("4/10", results)
        self.assertIn("40%", results)
        self.assertNotIn("待完整裁决", results)
        self.assertNotIn("尚未登记模型候选", results)
        self.assertNotIn("planned_not_run", results)
        self.assertNotIn("fixed_seen_development_sample", results)

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

    def test_canonical_showcase_is_default_and_history_requires_explicit_selection(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            canonical_path = (
                project_dir / "benchmarks" / "showcase" / "canonical-showcase-v1.json"
            )
            canonical_path.parent.mkdir(parents=True)
            canonical_path.write_text(
                json.dumps(_canonical_showcase_summary(), ensure_ascii=False),
                encoding="utf-8",
            )
            history_path = (
                project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
            )
            history_path.parent.mkdir(parents=True)
            history_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "experiment_type": "runtime_quality",
                        "title": "Historical Golden-10",
                        "status": "completed",
                        "reference_iteration": "R0",
                        "reference_metrics": {
                            "planned": 1,
                            "official_resolved": 1,
                            "official_decided": 1,
                        },
                        "iterations": [],
                    }
                ),
                encoding="utf-8",
            )

            sources = FileEvidenceCatalog(project_dir).evidence_sources()
            default_overview = _render_workspace_view(
                project_dir,
                source_key="",
                view="overview",
            )
            canonical_results = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )
            historical_results = _render_workspace_view(
                project_dir,
                source_key="evaluation-history",
                view="results",
            )
            legacy_bookmark = _render_evidence_html(project_dir, "benchmark")

        source_by_key = {source.key: source for source in sources}
        self.assertEqual(source_by_key["evaluation"].primary_path, canonical_path)
        self.assertEqual(source_by_key["evaluation-history"].primary_path, history_path)
        self.assertIn("NanoHarness · Canonical Showcase", default_overview)
        self.assertIn("showcase-quality-v1", default_overview)
        self.assertIn("待完整裁决", default_overview)
        self.assertNotIn("metric-value'>0/50", default_overview)
        self.assertIn("Golden-10", canonical_results)
        self.assertIn("开发与回归专用", canonical_results)
        self.assertIn("Infrastructure Smoke-5", canonical_results)
        self.assertIn("基础设施健康检查专用", canonical_results)
        self.assertIn("不进入质量 headline", canonical_results)
        self.assertIn("deterministic 50-case sample", canonical_results)
        self.assertNotIn("metric-value'>0/50", canonical_results)
        self.assertIn("历史归档 · Historical Golden-10", historical_results)
        self.assertIn("Canonical Showcase", legacy_bookmark)

    def test_fail_closed_selection_incident_is_history_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            canonical_path = (
                project_dir / "benchmarks" / "showcase" / "canonical-showcase-v1.json"
            )
            canonical_path.parent.mkdir(parents=True)
            canonical_path.write_text(
                json.dumps(_canonical_showcase_summary(), ensure_ascii=False),
                encoding="utf-8",
            )
            incident_path = (
                project_dir
                / "benchmarks"
                / "archive"
                / "quality-selection-v1-fail-closed.json"
            )
            incident_path.parent.mkdir(parents=True)
            incident_path.write_text(
                json.dumps(_quality_selection_incident_summary(), ensure_ascii=False),
                encoding="utf-8",
            )

            sources = FileEvidenceCatalog(project_dir).evidence_sources()
            default_results = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )
            history_overview = _render_workspace_view(
                project_dir,
                source_key="evaluation-history",
                view="overview",
            )
            history_results = _render_workspace_view(
                project_dir,
                source_key="evaluation-history",
                view="results",
            )

        source_by_key = {source.key: source for source in sources}
        self.assertEqual(source_by_key["evaluation"].primary_path, canonical_path)
        self.assertEqual(
            source_by_key["evaluation-history"].primary_path,
            incident_path,
        )
        self.assertIsNone(source_by_key["evaluation-history"].run_dir)
        self.assertEqual(source_by_key["evaluation-history"].trace_entries, ())
        self.assertIn("Canonical Showcase", default_results)
        self.assertNotIn("Fail-Closed Incident", default_results)
        self.assertIn("全量污染尾段之前", history_overview)
        self.assertIn("20/20 均有 finalized artifact", history_overview)
        self.assertIn("11/20", history_overview)
        self.assertIn("NO WINNER", history_results)
        self.assertIn("summarizer", history_results.lower())
        self.assertIn("正确性重跑 = 0", history_results)
        self.assertIn("不得从局部样本倒推 winner", history_results)
        self.assertNotIn("Official Pass@1", history_results)

    def test_canonical_score_requires_frozen_validated_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            summary = _canonical_showcase_summary()
            evaluation = summary["canonical_evaluation"]
            assert isinstance(evaluation, dict)
            evaluation.update(
                {
                    "status": "running",
                    "completed": 10,
                    "terminal_accounted": 10,
                    "official_evaluated": 10,
                    "empty_patch": 0,
                    "provider_infra": 0,
                    "evaluator_infra": 0,
                    "official_resolved": 4,
                    "evidence_validated": False,
                }
            )
            summary_path = (
                project_dir / "benchmarks" / "showcase" / "canonical-showcase-v1.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False),
                encoding="utf-8",
            )

            partial = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )
            evaluation.update(
                {
                    "status": "completed",
                    "completed": 50,
                    "terminal_accounted": 50,
                    "official_evaluated": 47,
                    "empty_patch": 3,
                    "provider_infra": 0,
                    "evaluator_infra": 0,
                    "official_resolved": 23,
                    "evidence_validated": True,
                }
            )
            summary["status"] = "completed"
            profile = summary["current_profile"]
            assert isinstance(profile, dict)
            profile["frozen"] = True
            profile["selected_model"] = "opencode-go/glm-5.2"
            evaluation["cohort_frozen"] = True
            evaluation["protocol_frozen"] = True
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False),
                encoding="utf-8",
            )
            complete = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )

        self.assertIn("待完整裁决", partial)
        self.assertNotIn("4/50", partial)
        self.assertIn("23/50", complete)

        self.assertTrue(_canonical_score_is_publishable(summary))
        invalid_variants = [
            (("status",), "running"),
            (("current_profile", "frozen"), False),
            (("current_profile", "frozen"), "true"),
            (("current_profile", "selected_model"), None),
            (("current_profile", "selected_model"), 1),
            (("canonical_evaluation", "status"), "running"),
            (("canonical_evaluation", "cohort_frozen"), False),
            (("canonical_evaluation", "cohort_frozen"), "true"),
            (("canonical_evaluation", "protocol_frozen"), False),
            (("canonical_evaluation", "evidence_validated"), False),
            (("canonical_evaluation", "completed"), 49),
            (("canonical_evaluation", "terminal_accounted"), 49),
            (("canonical_evaluation", "official_evaluated"), 46),
            (("canonical_evaluation", "empty_patch"), 4),
            (("canonical_evaluation", "provider_infra"), 1),
            (("canonical_evaluation", "evaluator_infra"), 1),
            (("canonical_evaluation", "official_resolved"), 48),
        ]
        for path, invalid_value in invalid_variants:
            with self.subTest(path=path):
                variant = json.loads(json.dumps(summary))
                target = variant
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = invalid_value
                self.assertFalse(_canonical_score_is_publishable(variant))

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
                        "available_tools": ["create_file"],
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
                    "tool_call": "create_file",
                    "tool_arguments": {"path": "settlement/new_rule.py"},
                },
                {
                    "step": 2,
                    "event_type": "tool_observation",
                    "success": True,
                    "observation": "created settlement/new_rule.py",
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
        self.assertEqual(turns[1].tool_decisions[0].tool_name, "create_file")

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
                            },
                            {
                                "event_type": "skill_selection",
                                "step": 0,
                                "skills": [
                                    {
                                        "name": "swebench_repair",
                                        "version": "3.0.0",
                                        "selection_reason": "explicit invocation",
                                        "required_tools": ["read_file", "replace_text"],
                                        "optional_tools": ["git_status"],
                                        "resources": [
                                            {
                                                "path": "references/failure-triage.md",
                                                "sha256": "a" * 64,
                                                "original_chars": 900,
                                                "disclosed_chars": 900,
                                                "truncated": False,
                                            }
                                        ],
                                    }
                                ],
                            },
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
        self.assertIn("写操作需要人工授权时", rendered)
        self.assertIn("入口控制 → 执行决策", rendered)
        self.assertIn("写操作防重复", rendered)
        self.assertIn("swebench_repair@3.0.0", rendered)
        self.assertIn("metadata 发现 → SKILL.md 激活", rendered)
        self.assertIn("references/failure-triage.md", rendered)
        self.assertIn("披露 900 / 900 字符", rendered)
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
        self.assertIn("持久状态变更操作启动前形成可恢复人工屏障", rendered)
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
        self.assertIn(
            "模型在最后一轮仍请求调用工具，因此运行时阻断了不完整产物", rendered
        )
        self.assertIn("增加步骤预算，或要求模型更早明确", rendered)
        self.assertIn("结果与证据", INDEX_HTML)

    def test_runtime_quality_summary_is_the_primary_evaluation_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            summary_path = (
                project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "experiment_type": "runtime_quality",
                        "title": "Golden-10 · Runtime 质量优化",
                        "status": "completed",
                        "question": "功能冻结后，怎样降低成本而不牺牲正确性？",
                        "success_criteria": "正确性不下降，效率指标显著改善。",
                        "case_ids": ["demo__case-1"],
                        "reference_iteration": "R0",
                        "accepted_iteration": None,
                        "accepted_metrics": None,
                        "reference_metrics": {
                            "planned": 1,
                            "official_resolved": 1,
                            "official_unresolved": 0,
                            "official_empty_or_skipped": 0,
                            "official_infrastructure_error": 0,
                            "official_decided": 1,
                            "patch_generated": 1,
                            "tool_calls": 10,
                            "failed_tool_calls": 1,
                            "cost_budget_stops": 0,
                            "provider_tokens": 1000,
                            "estimated_cost_usd": 0.01,
                        },
                        "iterations": [
                            {
                                "id": "R0",
                                "bottleneck": "基线",
                                "change": "无",
                                "decision": "baseline",
                                "cohort": "Golden-1",
                                "metrics": {
                                    "planned": 1,
                                    "official_resolved": 1,
                                    "official_unresolved": 0,
                                    "official_empty_or_skipped": 0,
                                    "official_infrastructure_error": 0,
                                    "official_decided": 1,
                                    "patch_generated": 1,
                                    "tool_calls": 20,
                                    "failed_tool_calls": 2,
                                    "provider_tokens": 2000,
                                    "estimated_cost_usd": 0.02,
                                },
                            },
                            {
                                "id": "R1",
                                "bottleneck": "上下文过长",
                                "change": "收紧压缩触发点",
                                "decision": "rejected",
                                "cohort": "Sentinel-1",
                                "metrics": {
                                    "planned": 1,
                                    "official_resolved": 0,
                                    "official_unresolved": 0,
                                    "official_empty_or_skipped": 1,
                                    "official_infrastructure_error": 0,
                                    "official_decided": 0,
                                    "patch_generated": 0,
                                    "tool_calls": 10,
                                    "failed_tool_calls": 1,
                                    "provider_tokens": 1000,
                                    "estimated_cost_usd": 0.01,
                                    "runtime_step_entries": 3,
                                    "llm_calls": 2,
                                },
                                "mechanism_check": {
                                    "context_assembly_count": 3,
                                    "create_file_visible_context_count": 0,
                                    "create_file_dropped_context_count": 3,
                                    "create_file_action_count": 0,
                                    "mechanism_result": "passed",
                                    "task_outcome_result": "failed",
                                },
                                "invalid_launch_excluded": {
                                    "reason": "Skill identity drift",
                                    "observed_but_excluded": "漂亮的产品源码 Patch",
                                    "provider_tokens_lower_bound": 123,
                                    "confirmed_cost_usd_lower_bound": 0.001,
                                    "excluded_from_all_gates_and_valid_metrics": True,
                                },
                            },
                        ],
                        "failure_pareto": [
                            {
                                "failure": "预算耗尽",
                                "count": 1,
                                "evidence": "停止原因来自 Trace。",
                            }
                        ],
                        "case_results": [
                            {
                                "case_id": "demo__case-1",
                                "R0": {
                                    "official_status": "official_resolved",
                                    "patch_generated": True,
                                    "stop_reason": "final_answer",
                                },
                                "R1": {
                                    "official_status": "official_eval_skipped_empty_patch",
                                    "patch_generated": False,
                                    "stop_reason": "cost_budget_exceeded",
                                },
                                "transition": "resolved_to_empty_skipped，按 gate 拒绝。",
                            }
                        ],
                        "historical_exploration": [
                            {
                                "id": "P0",
                                "scope": "Golden-1",
                                "finding": "候选 Patch 形成率发生变化。",
                                "claim_boundary": "没有完整 official 裁决，不进入正式基线。",
                            }
                        ],
                        "cost_and_time": {
                            "observed": [
                                {
                                    "iteration": "R0",
                                    "cohort": "Golden-1",
                                    "provider_tokens": 1000,
                                    "cost_usd": 0.01,
                                    "wall_minutes": 2.0,
                                    "summed_llm_latency_minutes": 1.0,
                                }
                            ],
                            "uncertainty": "并发墙钟时间不是 LLM latency 求和。",
                        },
                        "rollback": {
                            "commit": "rollback-demo",
                            "retained_measurement_hygiene_commit": "guard-demo",
                            "reason": "候选策略未通过 gate。",
                        },
                        "fixed_conditions": {"模型": "demo-model"},
                        "boundaries": ["固定小样本，不外推总体解决率"],
                        "decision_story": ["从失败 Pareto 选择第一瓶颈。"],
                        "conclusion": "R1 触发正确性回归，因此拒绝并回滚。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            catalog = FileEvidenceCatalog(project_dir)
            source = next(
                item for item in catalog.evidence_sources() if item.key == "evaluation"
            )
            overview = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="overview",
            )
            results = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )

        self.assertEqual(source.primary_path, summary_path)
        self.assertEqual(source.title, "Golden-10 · Runtime 质量优化")
        self.assertIn("正式 R0 解决 / 计划", overview)
        self.assertIn("1/1", overview)
        self.assertIn("正式 R0-R3", results)
        self.assertIn("Provider Token / 成本", results)
        self.assertIn("失败 Pareto", results)
        self.assertIn("逐题结果", results)
        self.assertIn("resolved_to_empty_skipped，按 gate 拒绝", results)
        self.assertIn("unresolved 0 · empty 1 · infra 0", results)
        self.assertIn("机制生效不等于任务成功", results)
        self.assertIn("3</td><td>0</td><td>3", results)
        self.assertIn("Step / LLM 3/2", results)
        self.assertIn("成本与时间", results)
        self.assertIn("2.0 min", results)
        self.assertIn("Skill identity drift", results)
        self.assertIn("漂亮的产品源码 Patch", results)
        self.assertIn("rollback-demo", results)
        self.assertIn("guard-demo", results)
        self.assertIn("Pre-R0 探索性预实验", results)
        self.assertIn("没有完整 official 裁决", results)
        self.assertIn("固定小样本，不外推总体解决率", results)

    def test_schema_v3_renders_phase2_first_and_retains_phase1(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            summary_path = (
                project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(_phase2_runtime_quality_summary(), ensure_ascii=False),
                encoding="utf-8",
            )

            overview = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="overview",
            )
            results = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )

        self.assertIn("Target 个案", overview)
        self.assertIn("0/2 → 2/2", overview)
        self.assertIn("正确性 Guards", overview)
        self.assertIn("3/3", overview)
        self.assertLess(
            results.index("PHASE 2 · OPERATION LEDGER TREATMENT"),
            results.index("PHASE 1 · 历史正式 R0-R3"),
        )
        self.assertIn("Phase 1 · 正式 R0-R3", results)
        self.assertIn("不与 Phase 2 合并", results)
        self.assertIn("post-hoc Case-level", results)
        self.assertIn("SWE-bench Verified 总体解决率提升", results)
        self.assertIn("class='badge ok'>已采纳", results)
        self.assertIn("5/10 → 6/10", results)
        self.assertIn("Phase 2 Token / LLM", results)
        self.assertIn("3,268,023 / 291", results)
        self.assertIn("总成本 $4.129692", results)
        self.assertIn("Target+Guards 777,258 Token / $0.905107", results)
        self.assertIn("Golden-10 2,490,765 Token / $3.224585", results)

    def test_schema_v3_pending_golden_metric_is_not_rendered_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            summary = _phase2_runtime_quality_summary()
            phase2 = summary["phase2"]
            assert isinstance(phase2, dict)
            golden = phase2["golden10_expansion"]
            assert isinstance(golden, dict)
            golden["status"] = "running"
            golden["treatment_metrics"] = {"planned": 10}
            golden["net_official_resolved_delta"] = None
            golden["baseline_resolved_regressions"] = None
            golden["case_results"] = []
            summary_path = (
                project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False),
                encoding="utf-8",
            )

            overview = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="overview",
            )
            results = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )

        self.assertIn("5/10 → 待运行", overview)
        self.assertIn("5/10 → 待运行", results)
        self.assertNotIn("5/10 → 0/10", overview)
        self.assertNotIn("5/10 → 0/10", results)
        self.assertIn("净变化 待运行 · 原 resolved 回归 待运行", results)

    def test_schema_v3_uses_only_phase2_trace_and_usage_identity(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as outside_tmp,
        ):
            project_dir = Path(tmp)
            outside_case = Path(outside_tmp) / "cases/external-case"
            outside_case.mkdir(parents=True)
            (outside_case / "trace.json").write_text(
                json.dumps({"task": "outside-trace", "events": []}),
                encoding="utf-8",
            )
            summary_path = (
                project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary = _phase2_runtime_quality_summary()
            phase2 = summary["phase2"]
            assert isinstance(phase2, dict)
            phase2["evidence_run_dirs"] = {
                "empty": [""],
                "outside": [outside_tmp],
                "target": [".agent_forge/phase2/target/run"],
            }
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False),
                encoding="utf-8",
            )
            phase1_case = project_dir / ".agent_forge/phase1/run/cases/old-case"
            phase2_case = project_dir / ".agent_forge/phase2/target/run/cases/new-case"
            phase1_case.mkdir(parents=True)
            phase2_case.mkdir(parents=True)
            (phase1_case / "trace.json").write_text(
                json.dumps({"task": "phase1-old-trace", "events": []}),
                encoding="utf-8",
            )
            (phase1_case / "usage.json").write_text(
                json.dumps({"summary": {"llm_calls": 99}}),
                encoding="utf-8",
            )
            phase2_trace = phase2_case / "trace.json"
            phase2_usage = phase2_case / "usage.json"
            phase2_trace.write_text(
                json.dumps({"task": "phase2-current-trace", "events": []}),
                encoding="utf-8",
            )
            phase2_usage.write_text(
                json.dumps({"summary": {"llm_calls": 2}}),
                encoding="utf-8",
            )

            source = next(
                item
                for item in FileEvidenceCatalog(project_dir).evidence_sources()
                if item.key == "evaluation"
            )
            timeline = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="timeline",
            )

        self.assertEqual(
            source.run_dir,
            project_dir / ".agent_forge/phase2/target/run",
        )
        self.assertEqual(source.usage_path, phase2_usage)
        self.assertEqual(source.trace_entries[0][1], phase2_trace)
        self.assertIn("Phase 2 · target · new-case", source.trace_entries[0][0])
        self.assertTrue(
            all("phase1" not in str(path) for _, path in source.trace_entries)
        )
        self.assertIn("Phase 2 · target · new-case", timeline)
        self.assertIn(str(phase2_trace), timeline)
        self.assertNotIn(str(phase1_case / "trace.json"), timeline)
        self.assertNotIn(str(outside_case / "trace.json"), timeline)

    def test_phase2_accepted_tone_does_not_accept_suffix_collisions(self):
        self.assertEqual(_tone_for_status("Phase 2 · accepted"), "ok")
        self.assertEqual(_tone_for_status("accepted"), "ok")
        self.assertEqual(_tone_for_status("adopted"), "ok")
        self.assertNotEqual(_tone_for_status("not_accepted"), "ok")
        self.assertNotEqual(_tone_for_status("unaccepted"), "ok")
        self.assertEqual(_tone_for_status("failed · accepted"), "bad")

    def test_legacy_runtime_quality_summary_fails_closed_as_pre_r0(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            summary_path = (
                project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "experiment_type": "runtime_quality",
                        "title": "旧 Golden-10 Runtime 质量优化",
                        "status": "completed",
                        "case_ids": ["demo__case-1"],
                        "accepted_iteration": "R2",
                        "accepted_metrics": {
                            "case_count": 1,
                            "confirmed_solved": 1,
                            "patch_generated": 1,
                        },
                        "iterations": [
                            {
                                "id": "R0",
                                "scope": "Golden-1",
                                "change": "64K Context",
                                "decision": "baseline",
                                "metrics": {
                                    "case_count": 1,
                                    "patch_generated": 0,
                                    "total_tokens": 100,
                                    "estimated_cost_usd": 0.01,
                                },
                            },
                            {
                                "id": "R2",
                                "scope": "Golden-1",
                                "change": "48K Context",
                                "decision": "accepted",
                                "metrics": {
                                    "case_count": 1,
                                    "patch_generated": 1,
                                    "total_tokens": 90,
                                    "estimated_cost_usd": 0.009,
                                },
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            catalog = FileEvidenceCatalog(project_dir)
            source = next(
                item for item in catalog.evidence_sources() if item.key == "evaluation"
            )
            overview = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="overview",
            )
            results = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )

        self.assertIn("exploratory_only", source.status)
        self.assertIn("Pre-R0", overview)
        self.assertIn("旧 accepted 标签", overview)
        self.assertIn("已撤回", overview)
        self.assertIn("Fail closed", results)
        self.assertIn("P0-P2 历史过程", results)
        self.assertNotIn("正式参考 R2", overview)
        self.assertNotIn("Official resolved / planned", results)

    def test_runtime_quality_pending_candidate_and_decided_fallback_are_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            summary_path = (
                project_dir / "benchmarks" / "runtime-quality" / "golden-10-v1.json"
            )
            summary_path.parent.mkdir(parents=True)
            reference_metrics = {
                "planned": 10,
                "official_resolved": 4,
                "official_unresolved": 3,
                "official_empty_or_skipped": 3,
                "official_infrastructure_error": 0,
            }
            summary_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "experiment_type": "runtime_quality",
                        "status": "running",
                        "reference_iteration": "R0",
                        "accepted_iteration": None,
                        "reference_metrics": reference_metrics,
                        "iterations": [
                            {
                                "id": "R0",
                                "cohort": "Golden-10",
                                "decision": "reference",
                                "metrics": reference_metrics,
                            },
                            {
                                "id": "R1",
                                "cohort": "Sentinel-4",
                                "decision": "pending",
                                "metrics": {
                                    "planned": 4,
                                    "official_resolved": 0,
                                    "official_unresolved": 0,
                                    "official_empty_or_skipped": 0,
                                },
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            overview = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="overview",
            )
            results = _render_workspace_view(
                project_dir,
                source_key="evaluation",
                view="results",
            )

        self.assertIn("官方裁决覆盖 7/10", overview)
        self.assertIn("未采纳；rejected 0，pending/other 1", results)
        self.assertNotIn("全部候选轮均拒绝并回滚", results)
        self.assertNotIn("最终处置", results)

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
