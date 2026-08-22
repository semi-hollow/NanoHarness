import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from apps.cli.parser import build_parser
from apps.workbench.presentation.http import (
    INDEX_HTML,
    _TRACE_EVENT_LABELS,
    _TRACE_EVENT_PURPOSES,
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

    def test_every_named_trace_event_has_a_business_reason(self):
        """新增事件不能只获得中文标签，还必须解释它保护的运行边界。"""

        dynamically_explained_events = {
            "task_state_checkpoint",
            "hook_check",
            "permission_check",
            "tool_execution_started",
            "tool_call",
            "tool_observation",
            "validation_evidence",
            "operation_ledger",
        }
        missing_reasons = set(_TRACE_EVENT_LABELS) - (
            set(_TRACE_EVENT_PURPOSES) | dynamically_explained_events
        )

        self.assertEqual(missing_reasons, set())

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

        adaptive = build_parser().parse_args(
            ["run", "choose the smallest safe strategy", "--agent-mode", "adaptive"]
        )
        self.assertEqual(adaptive.agent_mode, "adaptive")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["run", "legacy sequential route", "--agent-mode", "multi"]
            )

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

    def test_memory_cli_exposes_explicit_user_lifecycle(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "memory", "remember", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--key", result.stdout)
        self.assertIn("--content", result.stdout)
        self.assertIn("--scope", result.stdout)

        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "memory", "forget", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("memory_id", result.stdout)

        result = subprocess.run(
            [sys.executable, "-m", "agent_forge", "memory", "list", "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--json", result.stdout)

    def test_eval_commands_expose_feedback_and_dataset_export(self):
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

    def test_swebench_help_exposes_scorecard_experiment_controls(self):
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
        self.assertIn("--memory-max-chars", result.stdout)
        self.assertIn("--max-prompt-tokens", result.stdout)
        self.assertIn("--max-tool-calls-per-turn", result.stdout)
        self.assertIn("--temperature", result.stdout)
        self.assertIn("--thinking", result.stdout)
        self.assertIn("--reasoning-effort", result.stdout)
        self.assertIn("--official-namespace", result.stdout)

    def test_benchmark_case_explorer_is_public_and_non_executing(self):
        catalog = subprocess.run(
            [sys.executable, "-m", "agent_forge", "bench", "cases"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(catalog.returncode, 0, catalog.stderr)
        self.assertIn("候选全集：`500`", catalog.stdout)
        self.assertIn("SWE-bench/SWE-bench_Verified", catalog.stdout)
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
            latest = root / ".agent_forge" / "internal" / "index"
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

            self.assertIn("并行任务与最终收口", result_html)
            self.assertIn("runtime-audit", result_html)
            self.assertIn("最大并发数", usage_html)
            self.assertIn("2", usage_html)

    def test_run_evidence_view_renders_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "evidence")
        self.assertIn("运行证据总览", html)
        self.assertIn("运行全链路", html)
        self.assertIn("兼容旧格式", html)
        self.assertIn("查看本次触发的上下文、记忆、Skill 与工具适配信号", html)

    def test_overview_uses_product_outcome_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-overview"
            run_dir.mkdir(parents=True)
            latest = root / ".agent_forge" / "internal" / "index"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")
            (run_dir / "usage.json").write_text("{}", encoding="utf-8")
            (run_dir / "trace.json").write_text("{}", encoding="utf-8")

            html = _render_result_summary(root)
        self.assertIn("代码仓任务结果", html)

    def test_usage_dashboard_exposes_observed_adaptive_harness_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / ".agent_forge" / "runs" / "run-adaptive"
            run_dir.mkdir(parents=True)
            latest = root / ".agent_forge" / "internal" / "index"
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

        self.assertIn("自适应运行信号", html)
        self.assertIn("有证据的长期记忆召回", html)
        self.assertIn("工具调用规范化", html)
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
            latest = root / ".agent_forge" / "internal" / "index"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")

            html = _render_evidence_html(root, "evidence")

        self.assertIn("root cause evidence is sufficient", html)
        self.assertIn("生产者", html)
        self.assertIn("消费者", html)
        self.assertIn("协调器 + 验证者", html)

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
    {"step": 1, "event_type": "context_assembly", "context": {"permission_summary": "writes ask; dangerous commands denied", "active_skills": ["repo_orientation@1.0.0"], "tool_routing": {"allowed_tools": ["read_file"], "dropped_tools": ["run_command"], "metadata": {"read_file": {"mode": "read"}}}}},
    {"step": 3, "agent_name": "Implementer", "event_type": "permission_check", "permission_decision": "ask", "tool_call": "replace_text", "reason": "write needs approval"},
    {"event_type": "human_approval"},
    {"event_type": "recovery_decision"},
    {"step": 3, "event_type": "context_assembly", "context": {"permission_summary": "final step", "tool_routing": {"allowed_tools": [], "dropped_tools": []}}}
  ]
}
""",
                encoding="utf-8",
            )
            (run_dir / "run_request.json").write_text(
                json.dumps(
                    {"config": {"mcp_config_file": None, "mcp_allowed_tools": []}}
                ),
                encoding="utf-8",
            )
            task_state_dir = run_dir / "task_state"
            task_state_dir.mkdir()
            (task_state_dir / "task-1.json").write_text("{}", encoding="utf-8")
            latest = root / ".agent_forge" / "internal" / "index"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")

            html = _render_evidence_html(root, "controls")

        self.assertIn("隔离 Worktree", html)
        self.assertIn("拒绝", html)
        self.assertIn("read_file", html)
        self.assertIn("run_command", html)
        self.assertIn("repo_orientation@1.0.0", html)
        self.assertIn("1 / 1", html)
        self.assertIn("0 / 1 / 0", html)
        self.assertIn("写操作需要审批", html)
        self.assertIn("最终回答轮按设计关闭了工具调用", html)
        self.assertIn("本次未配置 MCP Server（不适用）", html)
        self.assertIn("覆盖写入 1 个当前状态文件", html)
        self.assertIn("不是每个 Turn 固定写一次", html)
        self.assertIn("class='fact-list'", html)

    def test_compare_evidence_view_has_clear_single_multi_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "compare")
        self.assertIn("单 Agent 与多 Agent 对比", html)
        self.assertIn("单 Agent", html)
        self.assertIn("多 Agent 协调器", html)
        self.assertIn("工程决策", html)
        self.assertIn("生成的产物", html)

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
            (root / ".agent_forge" / "internal" / "index").mkdir(parents=True)
            (root / ".agent_forge" / "internal" / "index" / "bench.txt").write_text(
                ".agent_forge/runs/swebench-demo\n",
                encoding="utf-8",
            )
            (run / "trace.json").write_text(
                """
{
  "run_id": "r1",
  "stop_reason": "final_answer",
  "events": [
    {"step": 0, "event_type": "model_capabilities", "success": true},
    {"step": 0, "event_type": "skill_selection", "success": true},
    {"step": 1, "event_type": "context_assembly", "success": true,
     "context": {"available_tools": ["git_diff", "read_file"],
                 "active_skills": ["repo_orientation@1.0.0"]}},
    {"step": 1, "event_type": "context_window", "success": true,
     "context_window": {"estimated_tokens_after": 12000,
                        "hard_input_limit": 32768, "compacted": false}},
    {"step": 1, "event_type": "hook_check", "success": true,
     "hook_stage": "before_model",
     "hook_result": {"decision": "allow", "reason": "all hooks deferred; default allow",
                     "decisions": [{"hook_name": "permission_policy", "decision": "defer"}]}},
    {"step": 1, "event_type": "llm_call", "success": true, "duration_ms": 12},
    {"step": 1, "event_type": "action", "success": true, "tool_call": "git_diff"},
    {"step": 1, "event_type": "hook_check", "success": true, "tool_call": "git_diff",
     "hook_result": {"decision": "allow", "reason": "read/list/search allowed",
                     "decisions": [{"hook_name": "permission_policy", "decision": "allow"}]}},
    {"step": 1, "event_type": "permission_check", "success": true,
     "tool_call": "git_diff", "permission_decision": "allow", "reason": "read/list/search allowed"},
    {"step": 1, "event_type": "operation_ledger", "success": true,
     "tool_call": "git_diff", "operation_status": "executed"},
    {"step": 1, "event_type": "tool_observation", "success": false, "tool_call": "git_diff"},
    {"step": 1, "event_type": "task_state_checkpoint", "success": true},
    {"step": 2, "event_type": "context_assembly", "success": true,
     "context": {"available_tools": ["git_diff", "read_file"],
                 "active_skills": ["repo_orientation@1.0.0"]}},
    {"step": 2, "event_type": "context_window", "success": true,
     "context_window": {"estimated_tokens_after": 14000,
                        "hard_input_limit": 32768, "compacted": false}},
    {"step": 2, "event_type": "llm_call", "success": true},
    {"step": 2, "event_type": "action", "success": true,
     "tool_call": "read_file", "tool_arguments": {"path": "target.py"}},
    {"step": 2, "event_type": "tool_observation", "success": true,
     "tool_call": "read_file"}
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

        self.assertIn("多 Agent Runtime", html)
        self.assertIn("单 Agent Runtime", html)
        self.assertLess(html.index("多 Agent Runtime"), html.index("单 Agent Runtime"))
        self.assertIn("上下文组装", html)
        self.assertIn("<code>context_assembly</code>", html)
        self.assertIn("第 1 轮 · 模型请求 git_diff", html)
        self.assertIn("存在失败", html)
        self.assertIn("运行级阶段", html)
        self.assertIn("不计入 Agent 轮次", html)
        self.assertIn("1 个 Agent 轮次", html)
        self.assertIn("2 个运行级事件", html)
        self.assertIn("AgentLoop 主链与 ToolCall 四层明细", html)
        self.assertIn("1 准备模型输入", html)
        self.assertIn("2 模型提出意图", html)
        self.assertIn("3 Runtime 处理意图", html)
        self.assertIn("4 结果回填", html)
        self.assertIn("入口控制 → 执行决策 → 受限执行 → 结果与恢复", html)
        self.assertIn("查看本轮原始 Trace 事件", html)
        self.assertIn("12,000 tokens · Δ +12,000 · 36.6% input budget", html)
        self.assertIn("class='timeline-turn key-turn' open", html)
        self.assertIn("class='timeline-turn'", html)
        self.assertNotIn("timeline-phase-grid", html)
        self.assertIn("git_diff · 1 次失败", html)
        self.assertIn("模型调用前处理器", html)
        self.assertIn("工具执行前规则", html)
        self.assertIn("模型调用 · 最终决定=允许 · 1 个处理器均无额外意见", html)
        self.assertIn("git_diff · 最终决定=允许 · permission_policy: 允许", html)
        self.assertIn("在上下文发送给模型前汇总外部策略", html)
        self.assertIn("在 git_diff 执行前汇总环境与权限策略", html)
        self.assertIn("全部处理器均无额外意见，按默认规则允许", html)
        self.assertIn("最终权限=允许", html)
        self.assertIn("操作状态=已执行", html)
        self.assertNotIn("底层 Trace 事实；用于展开排障", html)
        self.assertNotIn("all hooks deferred; default allow", html)
        self.assertIn("<td>12ms</td>", html)
        self.assertNotIn("固定六阶段", html)
        self.assertNotIn("排障：展开本轮原始事件", html)
        self.assertNotIn(">未观测<", html)
        self.assertNotIn("time: 12 ms", html)
        self.assertNotIn("<strong>Step 0</strong>", html)

    def test_ui_is_a_read_only_evidence_surface(self):
        self.assertIn("NanoHarness 证据工作台", INDEX_HTML)
        self.assertIn("执行过程", INDEX_HTML)
        self.assertIn("上下文与决策", INDEX_HTML)
        self.assertIn("结果与证据", INDEX_HTML)
        self.assertIn("选择运行证据", INDEX_HTML)
        self.assertIn('id="categorySelect"', INDEX_HTML)
        self.assertIn('id="runSelect"', INDEX_HTML)
        self.assertIn('id="itemSelect"', INDEX_HTML)
        self.assertIn("td .badge", INDEX_HTML)
        self.assertIn("white-space: normal", INDEX_HTML)
        self.assertIn("在运行证据与实验对比之间切换", INDEX_HTML)
        self.assertIn("loadEvidence('overview')", INDEX_HTML)
        self.assertNotIn("Failed Tool Delta", INDEX_HTML)
        self.assertNotIn("startJob(", INDEX_HTML)
        self.assertNotIn("/api/jobs", INDEX_HTML)

    def test_benchmark_view_renders_campaign_denominators_and_run_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            campaign = root / ".agent_forge/runs/campaigns/campaign-1"
            campaign.mkdir(parents=True)
            latest = root / ".agent_forge/internal/index"
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

        self.assertIn("重复实验结果", html)
        self.assertIn("1/1 (100.0%)", html)
        self.assertIn("治理增强版胜出", html)
        self.assertIn("case-1", html)
        self.assertIn("样本解决率以全部预注册 Case 为分母", html)
        self.assertIn("已评测补丁接受率", html)
        self.assertIn("这是 commissioning 子集，不是完整的五题三重复实验", html)

    def test_empty_benchmark_contract_does_not_claim_prompt_is_constant(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render_evidence_html(Path(tmp), "benchmark")

        self.assertIn("Case/任务输入", html)
        self.assertIn("Skill 注入上下文", html)
        self.assertIn("Verified 的 500 个公开任务", html)
        self.assertIn("人工分层选出 5 题", html)
        self.assertIn("不是随机样本", html)
        self.assertNotIn("temperature, prompt, budget", html)

    def test_latest_run_prefers_existing_swebench_over_verify_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / ".agent_forge" / "internal" / "index"
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
            latest = root / ".agent_forge" / "internal" / "index"
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
