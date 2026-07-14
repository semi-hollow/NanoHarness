import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_forge.cli.parser import build_parser
from agent_forge.runtime.adapters import JsonApprovalRepository
from agent_forge.workbench.presentation.commands import (
    build_agent_run_command as _build_agent_run_command,
    build_swebench_command as _build_swebench_command,
)
from agent_forge.workbench.presentation.http import (
    INDEX_HTML,
    _action_to_command,
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
        self.assertIn("bench", result.stdout)
        self.assertIn("ui", result.stdout)
        self.assertIn("approve", result.stdout)
        self.assertIn("resume", result.stdout)
        self.assertIn("eval", result.stdout)

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
        self.assertIn("--operation-ledger-root", result.stdout)

    def test_respond_help_exposes_durable_human_input_flags(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "respond", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("request_id", result.stdout)
        self.assertIn("--answer", result.stdout)
        self.assertIn("--cancel", result.stdout)
        self.assertIn("--human-input-root", result.stdout)

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

    def test_approve_cli_updates_pending_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonApprovalRepository(root / "approvals")
            request = store.request(
                tool_name="apply_patch",
                arguments={"path": "target.py", "old": "a", "new": "b"},
                action="apply_patch",
                command="",
                workspace=str(root),
                run_id="run-1",
                step=1,
                agent_name="CodingAgent",
                reason="write needs approval",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent_forge",
                    "approve",
                    request.operation_key,
                    "--approval-root",
                    str(root / "approvals"),
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("approved", result.stdout)
            self.assertEqual(store.get(request.operation_key).status, "approved")

    def test_ui_swebench_command_includes_agent_mode_defaults(self):
        command = _build_swebench_command(sys.executable, {}, regression=False)
        self.assertIn("--agent-mode", command.command)
        self.assertIn("compare", command.command)
        self.assertIn("--profile", command.command)
        self.assertIn("coding_fix", command.command)
        self.assertIn("--max-revision-rounds", command.command)
        self.assertIn("2", command.command)
        self.assertIn("--execution-mode", command.command)
        self.assertIn("worktree", command.command)
        self.assertIn("--network-policy", command.command)
        self.assertIn("deny", command.command)
        self.assertIn("--tool-routing", command.command)
        self.assertIn("task-aware", command.command)

    def test_ui_agent_command_supports_bounded_live_fanout(self):
        command = _build_agent_run_command(
            sys.executable,
            {
                "task": "Update independent runtime and test modules",
                "runAgentMode": "fanout",
                "fanoutPlan": "examples/fanout-plan.sample.json",
                "fanoutResume": ".agent_forge/runs/previous-run",
                "fanoutMaxWorkers": 3,
            },
        )

        self.assertIn("--agent-mode", command.command)
        self.assertIn("fanout", command.command)
        self.assertIn("--fanout-plan", command.command)
        self.assertIn("examples/fanout-plan.sample.json", command.command)
        self.assertIn("--fanout-resume", command.command)
        self.assertIn("--max-workers", command.command)
        self.assertIn("3", command.command)
        self.assertIn("--execution-mode", command.command)
        self.assertIn("worktree", command.command)
        self.assertIn("--no-keep-worktree", command.command)

        with self.assertRaisesRegex(ValueError, "relative project path"):
            _build_agent_run_command(
                sys.executable,
                {
                    "task": "Update independent runtime and test modules",
                    "runAgentMode": "fanout",
                    "fanoutPlan": "../outside.json",
                },
            )

        self.assertIn('id="runAgentMode"', INDEX_HTML)
        self.assertIn('id="fanoutPlan"', INDEX_HTML)

    def test_ui_and_report_locator_surface_live_fanout_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-fanout"
            fanout_dir = run_dir / "fanout"
            fanout_dir.mkdir(parents=True)
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")
            (fanout_dir / "fanout_report.md").write_text("# Live Fanout Report\n", encoding="utf-8")
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

            self.assertTrue(_latest_report_path(root).endswith("fanout/fanout_report.md"))
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
        self.assertIn("Claim Ladder", html)
        self.assertIn("Produced Artifacts", html)
        self.assertIn("No multi-agent artifacts", html)

    def test_run_evidence_renders_artifact_content_and_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-1"
            multi_dir = run_dir / "cases" / "case" / "multi_agent"
            artifact_path = multi_dir / "artifacts" / "review.md"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_text("# Review\nPASS: root cause evidence is sufficient.", encoding="utf-8")
            (multi_dir / "multi_agent_summary.json").write_text(
                """
{
  "status": "passed",
  "role_results": [{"role": "Reviewer", "decision": "PASS", "round_index": 0, "final_answer": "PASS"}],
  "artifacts": [{"id": "review", "role": "Reviewer", "kind": "review_report", "round_index": 0, "path": "%s"}]
}
""" % artifact_path,
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
            run = root / ".agent_forge" / "runs" / "swebench-demo" / "cases" / "case" / "multi"
            run.mkdir(parents=True)
            single_run = root / ".agent_forge" / "runs" / "swebench-demo" / "cases" / "case" / "single"
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
        self.assertLess(html.index("Multi-Agent Runtime"), html.index("Single-Agent Runtime"))
        self.assertIn("1. 上下文组装", html)
        self.assertIn("3. 动作解析", html)
        self.assertIn("tool: git_diff", html)
        self.assertIn("failed", html)
        self.assertNotIn(" · ", html)

    def test_ui_surfaces_runtime_control_and_feedback_operations(self):
        self.assertIn("NanoHarness Evidence Console", INDEX_HTML)
        self.assertIn("Runtime Controls", INDEX_HTML)
        self.assertIn("Orchestration", INDEX_HTML)
        self.assertIn("Evaluation", INDEX_HTML)
        self.assertIn("Feedback Loop", INDEX_HTML)
        self.assertIn('id="executionMode"', INDEX_HTML)
        self.assertIn('id="networkPolicy"', INDEX_HTML)
        self.assertIn('id="toolRouting"', INDEX_HTML)
        self.assertIn('id="feedbackOutcome"', INDEX_HTML)

    def test_ui_feedback_and_dataset_actions_target_latest_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "trace.json").write_text('{"events": []}', encoding="utf-8")
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")

            feedback = _action_to_command(
                "feedback",
                {
                    "feedbackOutcome": "needs_work",
                    "feedbackLabels": "validation-gap, tool-routing",
                    "feedbackNote": "candidate patch lacks official evaluation",
                },
                project_dir=root,
            )
            export = _action_to_command(
                "export_dataset",
                {"requireFeedback": True},
                project_dir=root,
            )

        self.assertIn(str(run_dir / "trace.json"), feedback.command)
        self.assertEqual(feedback.command.count("--label"), 2)
        self.assertIn("--note", feedback.command)
        self.assertIn(str(run_dir), export.command)
        self.assertIn("--require-feedback", export.command)
        self.assertNotIn("--include-patch", export.command)

    def test_latest_run_prefers_existing_swebench_over_verify_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            runs = root / ".agent_forge" / "runs"
            swebench = runs / "swebench-20260707-011718-4944f2e"
            (swebench / "cases" / "case").mkdir(parents=True)
            (swebench / "cases" / "case" / "comparison.json").write_text("{}", encoding="utf-8")
            verify = root / ".agent_forge" / "verify" / "runs" / "run-verify"
            verify.mkdir(parents=True)
            (verify / "trace.json").write_text("{}", encoding="utf-8")
            (latest / "bench.txt").write_text("/tmp/agent-forge-missing-bench-run\n", encoding="utf-8")
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
