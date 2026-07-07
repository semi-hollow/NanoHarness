import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_ui_swebench_command_includes_agent_mode_defaults(self):
        command = _build_swebench_command(sys.executable, {}, regression=False)
        self.assertIn("--agent-mode", command.command)
        self.assertIn("compare", command.command)
        self.assertIn("--profile", command.command)
        self.assertIn("coding_fix", command.command)
        self.assertIn("--max-revision-rounds", command.command)
        self.assertIn("2", command.command)

    def test_interview_evidence_view_renders_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "interview")
        self.assertIn("Interview Evidence", html)
        self.assertIn("5 分钟 Demo", html)
        self.assertIn("Golden Demo Capsule", html)
        self.assertIn("30 分钟学习路径", html)
        self.assertIn("docs/technical-defense/learn/三十分钟面试准备包.md", html)
        self.assertIn("Single vs Multi", html)
        self.assertIn("Safety", html)

    def test_compare_evidence_view_has_clear_single_multi_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "compare")
        self.assertIn("Single vs Multi 对比", html)
        self.assertIn("单 Agent", html)
        self.assertIn("多 Agent Coordinator", html)
        self.assertIn("不要声称 multi-agent 一定更强", html)

    def test_ui_labels_separate_interview_evidence_from_operations(self):
        from agent_forge.ui import INDEX_HTML

        self.assertIn("面试展示路径", INDEX_HTML)
        self.assertIn("真实运行操作", INDEX_HTML)
        self.assertIn("Single vs Multi 对比", INDEX_HTML)
        self.assertIn("成本与工具效率", INDEX_HTML)
        self.assertIn("执行时间线", INDEX_HTML)

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


if __name__ == "__main__":
    unittest.main()
