import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.approval import ApprovalStore
from agent_forge.ui import _build_swebench_command, _latest_run_dir, _render_evidence_html


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
            store = ApprovalStore(root / "approvals")
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

    def test_run_evidence_view_renders_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "evidence")
        self.assertIn("Run Evidence", html)
        self.assertIn("Reviewer Path", html)
        self.assertIn("Capability Reality Matrix", html)
        self.assertIn("Single vs Multi", html)
        self.assertIn("Safety", html)

    def test_compare_evidence_view_has_clear_single_multi_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "compare")
        self.assertIn("Single vs Multi 对比", html)
        self.assertIn("单 Agent", html)
        self.assertIn("多 Agent Coordinator", html)
        self.assertIn("不要假设 multi-agent 一定更强", html)

    def test_timeline_explains_scope_order_and_color_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / ".agent_forge" / "runs" / "swebench-demo" / "cases" / "case" / "multi"
            run.mkdir(parents=True)
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

            html = _render_evidence_html(root, "timeline")

        self.assertIn("当前展示：multi-agent trace", html)
        self.assertIn("按 trace.json 记录顺序展示", html)
        self.assertIn("1. 上下文组装", html)
        self.assertIn("3. 动作解析", html)
        self.assertIn("tool: git_diff", html)
        self.assertIn("红色表示失败", html)
        self.assertNotIn(" · ", html)

    def test_ui_labels_separate_run_evidence_from_operations(self):
        from agent_forge.ui import INDEX_HTML

        self.assertIn("证据审阅路径", INDEX_HTML)
        self.assertIn("真实运行操作", INDEX_HTML)
        self.assertIn("Single vs Multi 对比", INDEX_HTML)
        self.assertIn("成本与工具效率", INDEX_HTML)
        self.assertIn("执行时间线", INDEX_HTML)
        self.assertIn("显示操作面板", INDEX_HTML)
        self.assertIn("专注展示", INDEX_HTML)
        self.assertIn("显示状态栏", INDEX_HTML)

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
