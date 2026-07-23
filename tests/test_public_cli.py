import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_forge.cli.parser import build_parser
from agent_forge.workbench.presentation.http import (
    INDEX_HTML,
    _latest_report_path,
    _latest_run_dir,
    _render_evidence_html,
    _render_result_summary,
    _render_usage_dashboard,
)


class PublicCliSmokeTest(unittest.TestCase):
    """Keep only the user-facing smoke check in the repo.

    The project effect proof is SWE-bench, not a large author-created unit-test
    suite. This test only protects the public entrypoint from obvious import or
    argparse breakage.
    """

    def test_public_cli_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("run", "inspect", "demo", "resume", "bench", "ui"):
            self.assertIn(command, result.stdout)
        for legacy in ("approve", "respond", "eval", "showcase", "memory", "tui"):
            self.assertNotIn(legacy, result.stdout)

    def test_duplicate_legacy_commands_are_not_parseable(self):
        parser = build_parser()
        for command in ("report", "replay", "approve", "respond", "showcase", "tui"):
            with (
                self.subTest(command=command),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args([command])

    def test_run_help_exposes_resume_and_manual_approval_flags(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "run", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--resume-state", result.stdout)
        self.assertIn("--no-auto-approve-writes", result.stdout)
        self.assertIn("--approval-root", result.stdout)
        self.assertIn("--operation-ledger-root", result.stdout)
        self.assertIn("--execution-mode", result.stdout)
        self.assertIn("container", result.stdout)
        self.assertIn("--network-policy", result.stdout)
        self.assertIn("--no-keep-worktree", result.stdout)
        self.assertIn("--tool-routing", result.stdout)
        self.assertIn("--container-image", result.stdout)
        self.assertIn("--container-cpus", result.stdout)
        self.assertIn("--container-memory", result.stdout)
        self.assertIn("--container-pids-limit", result.stdout)
        self.assertIn("--fanout-plan", result.stdout)
        self.assertIn("--fanout-resume", result.stdout)
        self.assertIn("--max-workers", result.stdout)
        self.assertIn("--max-prompt-tokens", result.stdout)
        self.assertIn("--reserved-output-tokens", result.stdout)
        self.assertIn("--memory-root", result.stdout)
        self.assertIn("--max-tool-calls-per-turn", result.stdout)
        self.assertIn("--temperature", result.stdout)
        self.assertIn("--thinking", result.stdout)
        self.assertIn("--reasoning-effort", result.stdout)

        args = build_parser().parse_args(
            [
                "run",
                "split this work",
                "--agent-mode",
                "fanout",
                "--fanout-plan",
                "examples/fanout-plan.sample.json",
            ]
        )
        self.assertEqual(args.agent_mode, "fanout")

    def test_resume_help_exposes_resume_specific_flags(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "resume", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("run_dir", result.stdout)
        self.assertIn("--task", result.stdout)
        self.assertIn("--answer", result.stdout)
        self.assertIn("--decision", result.stdout)
        self.assertIn("--operation-ledger-root", result.stdout)

    def test_memory_cli_exposes_authority_lifecycle(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "memory", "propose", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--kind", result.stdout)
        self.assertIn("--scope", result.stdout)
        self.assertIn("--confidence", result.stdout)

        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "memory", "promote", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--evidence", result.stdout)

    def test_eval_commands_expose_feedback_and_dataset_export(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "eval", "mini-cases", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--case", result.stdout)
        self.assertIn("--evidence", result.stdout)
        self.assertIn("--output-root", result.stdout)

        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "eval", "feedback", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--outcome", result.stdout)
        self.assertIn("--label", result.stdout)

        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "eval", "export-dataset", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--require-feedback", result.stdout)
        self.assertIn("--include-patch", result.stdout)

        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "eval", "ablation", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("control", result.stdout)
        self.assertIn("treatment", result.stdout)
        self.assertIn("--factor", result.stdout)

    def test_swebench_help_exposes_scorecard_ablation_factor(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "bench", "swebench", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--tool-routing", result.stdout)
        self.assertIn("--regression-set", result.stdout)
        self.assertIn("--execution-mode", result.stdout)
        self.assertIn("--container-image", result.stdout)
        self.assertIn("--skills", result.stdout)
        self.assertIn("--memory-recall-limit", result.stdout)
        self.assertIn("--max-prompt-tokens", result.stdout)
        self.assertIn("--max-tool-calls-per-turn", result.stdout)
        self.assertIn("--temperature", result.stdout)
        self.assertIn("--thinking", result.stdout)
        self.assertIn("--reasoning-effort", result.stdout)

    def test_benchmark_case_explorer_is_public_and_non_executing(self):
        catalog = subprocess.run(
            [sys.executable, "-m", "agent_forge", "bench", "cases"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(catalog.returncode, 0, catalog.stderr)
        self.assertIn("候选全集：`300`", catalog.stdout)
        self.assertIn("astropy__astropy-12907", catalog.stdout)

        case_help = subprocess.run(
            [sys.executable, "-m", "agent_forge", "bench", "case", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(case_help.returncode, 0, case_help.stderr)
        self.assertIn("--show-test-patch", case_help.stdout)
        self.assertIn("--show-gold", case_help.stdout)

    def test_ui_and_report_locator_surface_live_fanout_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-fanout"
            fanout_dir = run_dir / "fanout"
            fanout_dir.mkdir(parents=True)
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")
            (fanout_dir / "fanout_report.md").write_text(
                "# Live Fanout Report\n", encoding="utf-8"
            )
            (fanout_dir / "fanout_summary.json").write_text(
                """
{
  "goal": "audit runtime and safety",
  "status": "passed",
  "batches": [["runtime-audit", "safety-audit"]],
  "merged_task_ids": ["runtime-audit", "safety-audit"],
  "final_decision": "PASS",
  "metrics": {
    "task_count": 2,
    "completed_count": 2,
    "max_workers": 2,
    "wall_time_ms": 1200,
    "summed_worker_duration_ms": 2100,
    "llm_calls": 3,
    "total_tokens": 900,
    "estimated_cost_usd": 0.01,
    "tool_calls": 4,
    "failed_tool_calls": 0
  },
  "results": [
    {"task_id": "runtime-audit", "status": "completed", "resumed": false, "touched_files": []},
    {"task_id": "safety-audit", "status": "completed", "resumed": false, "touched_files": []}
  ]
}
""",
                encoding="utf-8",
            )

            report_path = Path(_latest_report_path(root))
            self.assertEqual(report_path.name, "fanout_report.md")
            self.assertEqual(report_path.parent.name, "fanout")
            result_html = _render_result_summary(root)
            usage_html = _render_usage_dashboard(root)

            self.assertIn("Live Fanout", result_html)
            self.assertIn("runtime-audit", result_html)
            self.assertIn("Max Workers", usage_html)
            self.assertIn("2", usage_html)

    def test_run_evidence_view_renders_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "evidence")
        self.assertIn("Runtime Evidence Overview", html)
        self.assertIn("Runtime Pipeline", html)
        self.assertIn("Adaptive Runtime Evidence", html)
        self.assertIn("Claim Ladder", html)
        self.assertIn("Produced Artifacts", html)
        self.assertIn("No multi-agent artifacts", html)

    def test_overview_uses_product_outcome_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-overview"
            run_dir.mkdir(parents=True)
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")
            (run_dir / "usage.json").write_text("{}", encoding="utf-8")
            (run_dir / "trace.json").write_text("{}", encoding="utf-8")

            html = _render_result_summary(root)
        self.assertIn("Repository Task Outcome", html)

    def test_usage_dashboard_exposes_observed_adaptive_harness_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-adaptive"
            run_dir.mkdir(parents=True)
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")
            (run_dir / "usage.json").write_text(
                """
{
  "summary": {
    "llm_calls": 2,
    "total_tokens": 120,
    "cache_hit_rate": 0,
    "estimated_cost_usd": 0.01,
    "llm_latency_ms": 20,
    "failed_tool_calls": 0,
    "compacted_context_turns": 1,
    "context_overflow_recoveries": 1,
    "memory_recalled": 2,
    "tool_call_repairs": 1,
    "bounded_tool_call_bursts": 1,
    "active_skills": ["targeted_code_edit"]
  },
  "steps": [],
  "context_breakdown": {"section_chars": {}},
  "tool_efficiency": {"by_tool": {}}
}
""",
                encoding="utf-8",
            )

            html = _render_usage_dashboard(root)

        self.assertIn("Adaptive Harness Signals", html)
        self.assertIn("Evidence-backed memory recall", html)
        self.assertIn("Tool-call normalization", html)
        self.assertIn("targeted_code_edit", html)

    def test_run_evidence_renders_artifact_content_and_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-1"
            multi_dir = run_dir / "cases" / "case" / "multi_agent"
            artifact_path = multi_dir / "artifacts" / "review.md"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_text(
                "# Review\nPASS: root cause evidence is sufficient.", encoding="utf-8"
            )
            (multi_dir / "multi_agent_summary.json").write_text(
                """
{
  "status": "passed",
  "role_results": [{"role": "Reviewer", "decision": "PASS", "round_index": 0, "final_answer": "PASS"}],
  "artifacts": [{"id": "review", "role": "Reviewer", "kind": "review_report", "round_index": 0, "path": "%s"}]
}
"""
                % artifact_path.as_posix(),
                encoding="utf-8",
            )
            (run_dir / "trace.json").write_text('{"events": []}', encoding="utf-8")
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")

            html = _render_evidence_html(root, "evidence")

        self.assertIn("root cause evidence is sufficient", html)
        self.assertIn("producer", html)
        self.assertIn("consumer", html)
        self.assertIn("Coordinator + Verifier", html)

    def test_runtime_controls_only_claim_events_observed_in_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "trace.json").write_text(
                """
{
  "events": [
    {"event_type": "task_state_checkpoint", "task_state": {"metadata": {"execution_environment": {"mode": "worktree", "network_policy": "deny", "active_workspace": "/snapshot"}}}},
    {"event_type": "context_assembly", "context": {"permission_summary": "writes ask", "active_skills": ["repo_orientation@1.0.0"], "tool_routing": {"allowed_tools": ["read_file"], "dropped_tools": ["run_command"]}}},
    {"step": 3, "agent_name": "Implementer", "event_type": "permission_check", "permission_decision": "ask", "tool_call": "apply_patch", "reason": "write needs approval"},
    {"event_type": "human_approval"},
    {"event_type": "recovery_decision"}
  ]
}
""",
                encoding="utf-8",
            )
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")

            html = _render_evidence_html(root, "controls")

        self.assertIn("worktree", html)
        self.assertIn("deny", html)
        self.assertIn("read_file", html)
        self.assertIn("run_command", html)
        self.assertIn("repo_orientation@1.0.0", html)
        self.assertIn("1 / 1", html)
        self.assertIn("0 / 1 / 0", html)
        self.assertIn("write needs approval", html)

    def test_compare_evidence_view_has_clear_single_multi_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "compare")
        self.assertIn("Single vs Multi 对比", html)
        self.assertIn("单 Agent", html)
        self.assertIn("多 Agent Coordinator", html)
        self.assertIn("Engineering Decision", html)
        self.assertIn("Produced Artifacts", html)

    def test_timeline_explains_scope_order_and_color_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = (
                root
                / ".agent_forge"
                / "runs"
                / "swebench-demo"
                / "cases"
                / "case"
                / "multi"
            )
            run.mkdir(parents=True)
            single_run = (
                root
                / ".agent_forge"
                / "runs"
                / "swebench-demo"
                / "cases"
                / "case"
                / "single"
            )
            single_run.mkdir(parents=True)
            (root / ".agent_forge" / "latest").mkdir(parents=True)
            (root / ".agent_forge" / "latest" / "bench.txt").write_text(
                ".agent_forge/runs/swebench-demo\n",
                encoding="utf-8",
            )
            (run / "trace.json").write_text(
                """
{
  "run_id": "r1",
  "stop_reason": "final_answer",
  "events": [
    {"step": 1, "event_type": "context_assembly", "success": true},
    {"step": 1, "event_type": "llm_call", "success": true, "duration_ms": 12},
    {"step": 1, "event_type": "action", "success": true, "tool_call": "git_diff"},
    {"step": 1, "event_type": "tool_observation", "success": false, "tool_call": "git_diff"}
  ]
}
""",
                encoding="utf-8",
            )
            (single_run / "trace.json").write_text(
                """
{
  "run_id": "r2",
  "stop_reason": "final_answer",
  "events": [
    {"step": 1, "event_type": "llm_call", "success": true},
    {"step": 1, "event_type": "action", "success": true, "tool_call": "read_file"}
  ]
}
""",
                encoding="utf-8",
            )

            html = _render_evidence_html(root, "timeline")

        self.assertIn("Multi-Agent Runtime", html)
        self.assertIn("Single-Agent Runtime", html)
        self.assertLess(
            html.index("Multi-Agent Runtime"), html.index("Single-Agent Runtime")
        )
        self.assertIn("1. 上下文组装", html)
        self.assertIn("3. 动作解析", html)
        self.assertIn("tool: git_diff", html)
        self.assertIn("failed", html)
        self.assertNotIn(" · ", html)

    def test_ui_is_a_read_only_evidence_surface(self):
        self.assertIn("NanoHarness Workbench", INDEX_HTML)
        self.assertIn("Run Evidence", INDEX_HTML)
        self.assertIn("Benchmark", INDEX_HTML)
        self.assertIn("Runtime Controls", INDEX_HTML)
        self.assertIn("Orchestration", INDEX_HTML)
        self.assertIn("Evaluation", INDEX_HTML)
        self.assertIn("Feedback Loop", INDEX_HTML)
        self.assertIn("td .badge", INDEX_HTML)
        self.assertIn("white-space: normal", INDEX_HTML)
        self.assertIn("Read-only Run Story", INDEX_HTML)
        self.assertNotIn("startJob(", INDEX_HTML)
        self.assertNotIn("/api/jobs", INDEX_HTML)

    def test_benchmark_view_renders_campaign_denominators_and_run_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            campaign = root / ".agent_forge/campaigns/campaign-1"
            campaign.mkdir(parents=True)
            latest = root / ".agent_forge/latest"
            latest.mkdir(parents=True)
            (latest / "campaign.txt").write_text(str(campaign), encoding="utf-8")
            (campaign / "campaign.json").write_text(
                """
{
  "schema_version": 1,
  "campaign_id": "campaign-1",
  "config_digest": "digest",
  "config": {"variants": [{"name": "minimal-control"}, {"name": "governed-runtime"}]},
  "source": {"revision": "abcdef123456", "branch": "master", "dirty": false},
  "created_at": "now",
  "updated_at": "now",
  "status": "completed",
  "records": [
    {"key": "case-1-r1-min", "ordinal": 1, "case_id": "case-1", "repetition": 1, "variant": "minimal-control", "status": "completed", "attempts": 1, "run_id": "run-1", "run_dir": "/tmp/run-1", "scorecard_sha256": "one", "evidence": {"patch_generated": true, "official_evaluation_status": "official_eval_failed", "failure_class": "official_eval_failed"}},
    {"key": "case-1-r1-gov", "ordinal": 2, "case_id": "case-1", "repetition": 1, "variant": "governed-runtime", "status": "completed", "attempts": 1, "run_id": "run-2", "run_dir": "/tmp/run-2", "scorecard_sha256": "two", "evidence": {"patch_generated": true, "official_evaluation_status": "official_resolved", "failure_class": "official_resolved"}}
  ]
}
""",
                encoding="utf-8",
            )
            (campaign / "campaign_summary.json").write_text(
                """
{
  "campaign_id": "campaign-1",
  "status": "completed",
  "source": {"revision": "abcdef123456", "branch": "master"},
  "config_digest": "digest",
  "planned_runs": 2,
  "status_counts": {"completed": 2},
  "variants": {
    "minimal-control": {"planned": 1, "completed": 1, "patch_generated": 1, "local_verified": 0, "official_evaluated": 1, "official_resolved": 0, "total_tokens": 100, "estimated_cost_usd": 0.01, "failed_tool_calls": 1},
    "governed-runtime": {"planned": 1, "completed": 1, "patch_generated": 1, "local_verified": 1, "official_evaluated": 1, "official_resolved": 1, "total_tokens": 120, "estimated_cost_usd": 0.02, "failed_tool_calls": 0}
  },
  "paired_official": {"evaluated_pairs": 1, "wins": {"minimal-control": 0, "governed-runtime": 1}, "ties": 0}
}
""",
                encoding="utf-8",
            )

            html = _render_evidence_html(root, "benchmark")

        self.assertIn("Repeated Runtime Evidence", html)
        self.assertIn("1/1 (100.0%)", html)
        self.assertIn("Governed Wins", html)
        self.assertIn("case-1", html)
        self.assertIn("Official resolved rate uses only", html)

    def test_empty_benchmark_contract_does_not_claim_prompt_is_constant(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "benchmark")

        self.assertIn("case/task input", html)
        self.assertIn("Skill-injected context", html)
        self.assertNotIn("temperature, prompt, budget", html)

    def test_latest_run_prefers_existing_swebench_over_verify_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            runs = root / ".agent_forge" / "runs"
            swebench = runs / "swebench-20260707-011718-4944f2e"
            (swebench / "cases" / "case").mkdir(parents=True)
            (swebench / "cases" / "case" / "comparison.json").write_text(
                "{}", encoding="utf-8"
            )
            verify = root / ".agent_forge" / "verify" / "runs" / "run-verify"
            verify.mkdir(parents=True)
            (verify / "trace.json").write_text("{}", encoding="utf-8")
            (latest / "bench.txt").write_text(
                "/tmp/agent-forge-missing-bench-run\n", encoding="utf-8"
            )
            (latest / "run.txt").write_text(str(verify), encoding="utf-8")

            self.assertEqual(_latest_run_dir(root), swebench)

    def test_latest_run_uses_newer_future_run_over_old_valid_bench_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            runs = root / ".agent_forge" / "runs"
            old_bench = runs / "swebench-old"
            future_run = runs / "run-future-agent"
            old_bench.mkdir(parents=True)
            future_run.mkdir(parents=True)
            (old_bench / "trace.json").write_text("{}", encoding="utf-8")
            (future_run / "trace.json").write_text("{}", encoding="utf-8")
            os.utime(old_bench, (100, 100))
            os.utime(future_run, (200, 200))
            (latest / "bench.txt").write_text(str(old_bench), encoding="utf-8")

            self.assertEqual(_latest_run_dir(root), future_run)


if __name__ == "__main__":
    unittest.main()
