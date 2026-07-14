import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.cli.parser import build_parser
from agent_forge.cli.repository import run_repository_task
from agent_forge.multi_agent.adapters.local_worker import _finalizer_task
from agent_forge.multi_agent.domain.live import FanoutPlan, LiveSubagentResult
from agent_forge.multi_agent.wiring import build_live_fanout
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import JsonHumanInputRepository
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.wiring import build_registry
from agent_forge.safety.guardrails import input_guardrail


class EditingLLM:
    last_usage = None

    def __init__(self, fail_task="", escape_scope=False, create_new=False):
        self.calls = 0
        self.fail_task = fail_task
        self.escape_scope = escape_scope
        self.create_new = create_new

    def chat(self, messages, tools):
        self.calls += 1
        prompt = "\n".join(message.content or "" for message in messages)
        if "FanoutVerifier" in prompt:
            return AgentResponse("PASS\nintegrated artifacts are present", [])
        task_id = "alpha" if "task_id=alpha" in prompt else "beta" if "task_id=beta" in prompt else ""
        if task_id == self.fail_task:
            return AgentResponse("blocked: fixture failure", [])
        if self.calls == 1:
            if self.create_new and task_id == "alpha":
                return AgentResponse(
                    None,
                    [
                        ToolCall(
                            "write-alpha",
                            "write_file",
                            {"path": "new.py", "content": "value = 'new'\n"},
                        )
                    ],
                )
            path = "b.py" if self.escape_scope and task_id == "alpha" else f"{task_id[0]}.py"
            return AgentResponse(
                None,
                [
                    ToolCall(
                        f"patch-{task_id}",
                        "apply_patch",
                        {"path": path, "old": "value = 1\n", "new": f"value = '{task_id}'\n"},
                    )
                ],
            )
        return AgentResponse(f"completed {task_id}", [])


class AskThenEditLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        prompt = "\n".join(message.content or "" for message in messages)
        if "FanoutVerifier" in prompt:
            return AgentResponse("PASS\nintegration verified", [])
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "ask-alpha",
                        "ask_human",
                        {"question": "Use the compatibility implementation?", "choices": ["yes", "no"]},
                    )
                ],
            )
        if self.calls == 2:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "patch-alpha",
                        "apply_patch",
                        {"path": "a.py", "old": "value = 1\n", "new": "value = 'approved'\n"},
                    )
                ],
            )
        return AgentResponse("completed alpha with operator input", [])


class BudgetAwareLLM:
    last_usage = None

    def __init__(self):
        self.worker_calls = 0

    def chat(self, messages, tools):
        prompt = "\n".join(message.content or "" for message in messages)
        if "FanoutVerifier" in prompt:
            return AgentResponse("PASS\nworker respected its structured step budget", [])
        self.worker_calls += 1
        if tools:
            return AgentResponse(
                None,
                [ToolCall(f"status-{self.worker_calls}", "git_status", {})],
            )
        return AgentResponse("completed within the task budget", [])


class DiffInspectingLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        prompt = "\n".join(message.content or "" for message in messages)
        if "FanoutVerifier" in prompt:
            if self.calls == 1:
                return AgentResponse(None, [ToolCall("inspect-candidate", "git_diff", {})])
            if "diff --git" in prompt and "value = 'alpha'" in prompt:
                return AgentResponse("PASS\nintegrated candidate diff is directly visible", [])
            return AgentResponse("BLOCKED\nintegrated candidate diff was not visible", [])
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "patch-alpha",
                        "apply_patch",
                        {"path": "a.py", "old": "value = 1\n", "new": "value = 'alpha'\n"},
                    )
                ],
            )
        return AgentResponse("completed alpha", [])


def _init_repo(root: Path) -> str:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    (root / "a.py").write_text("value = 1\n", encoding="utf-8")
    (root / "b.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py", "b.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _plan() -> FanoutPlan:
    return FanoutPlan.from_mapping(
        {
            "goal": "Update two independent modules",
            "tasks": [
                {
                    "id": "alpha",
                    "task": "implement alpha in a.py",
                    "write_scope": ["a.py"],
                    "allowed_tools": ["read_file", "apply_patch", "git_diff"],
                },
                {
                    "id": "beta",
                    "task": "implement beta in b.py",
                    "write_scope": ["b.py"],
                    "allowed_tools": ["read_file", "apply_patch", "git_diff"],
                },
            ],
        }
    )


class LiveFanoutTest(unittest.TestCase):
    def test_finalizer_can_inspect_integrated_candidate_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            plan = FanoutPlan.from_mapping(
                {
                    "goal": "update alpha",
                    "tasks": [
                        {
                            "id": "alpha",
                            "task": "update a.py",
                            "write_scope": ["a.py"],
                            "allowed_tools": ["apply_patch", "git_diff"],
                        }
                    ],
                }
            )

            summary = build_live_fanout(
                plan=plan,
                base_config=RuntimeConfig(workspace=str(repo), max_steps=3),
                trace=TraceRecorder(str(root / "run" / "trace.json")),
                run_dir=root / "run",
                llm_factory=DiffInspectingLLM,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=1,
            ).run()

            self.assertEqual(summary.status, "passed")
            self.assertEqual(summary.final_decision, "PASS")
            finalizer_trace = json.loads(Path(summary.finalizer_trace_path).read_text(encoding="utf-8"))
            observations = [
                event.get("observation", "")
                for event in finalizer_trace["events"]
                if event["event_type"] == "tool_observation"
            ]
            self.assertTrue(any("diff --git" in item for item in observations))

    def test_plan_validates_and_serializes_per_task_step_budget(self):
        plan = FanoutPlan.from_mapping(
            {
                "goal": "bounded task",
                "tasks": [{"id": "alpha", "task": "inspect status", "max_steps": 2}],
            }
        )

        self.assertEqual(plan.tasks[0].max_steps, 2)
        self.assertEqual(plan.to_dict()["tasks"][0]["max_steps"], 2)
        for invalid in (1, 33, True, "two"):
            with self.subTest(max_steps=invalid):
                with self.assertRaisesRegex(ValueError, "max_steps"):
                    FanoutPlan.from_mapping(
                        {
                            "goal": "bad budget",
                            "tasks": [
                                {
                                    "id": "alpha",
                                    "task": "inspect status",
                                    "max_steps": invalid,
                                }
                            ],
                        }
                    )

    def test_worker_uses_plan_step_budget_instead_of_global_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            plan = FanoutPlan.from_mapping(
                {
                    "goal": "bounded worker",
                    "tasks": [
                        {
                            "id": "alpha",
                            "task": "inspect repository status once",
                            "allowed_tools": ["git_status"],
                            "max_steps": 2,
                        }
                    ],
                }
            )
            created_llms = []

            def llm_factory():
                llm = BudgetAwareLLM()
                created_llms.append(llm)
                return llm

            summary = build_live_fanout(
                plan=plan,
                base_config=RuntimeConfig(workspace=str(repo), max_steps=12),
                trace=TraceRecorder(str(root / "run" / "trace.json")),
                run_dir=root / "run",
                llm_factory=llm_factory,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=1,
            ).run()

            self.assertEqual(summary.status, "passed")
            self.assertEqual(summary.results[0].status, "completed")
            self.assertEqual(summary.results[0].usage_summary["llm_calls"], 2)
            self.assertEqual(created_llms[0].worker_calls, 2)

    def test_plan_rejects_path_escape_and_unknown_dependencies(self):
        for task_id in (".", ".."):
            with self.subTest(task_id=task_id):
                with self.assertRaisesRegex(ValueError, "invalid fanout task id"):
                    FanoutPlan.from_mapping(
                        {"goal": "bad", "tasks": [{"id": task_id, "task": "bad"}]}
                    )
        with self.assertRaisesRegex(ValueError, "relative workspace path"):
            FanoutPlan.from_mapping(
                {"goal": "bad", "tasks": [{"id": "bad", "task": "bad", "write_scope": ["../outside"]}]}
            )
        with self.assertRaisesRegex(ValueError, "unknown dependencies"):
            FanoutPlan.from_mapping(
                {
                    "goal": "bad",
                    "tasks": [{"id": "bad", "task": "bad", "depends_on": ["missing"]}],
                }
            )
        with self.assertRaisesRegex(ValueError, "expected_artifact"):
            FanoutPlan.from_mapping(
                {
                    "goal": "bad",
                    "tasks": [
                        {
                            "id": "bad",
                            "task": "bad",
                            "expected_artifact": "../../outside",
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, "allowed_tools must be a list"):
            FanoutPlan.from_mapping(
                {
                    "goal": "bad",
                    "tasks": [
                        {
                            "id": "bad",
                            "task": "bad",
                            "allowed_tools": "read_file",
                        }
                    ],
                }
            )

    def test_plan_preserves_dot_directory_scopes(self):
        plan = FanoutPlan.from_mapping(
            {
                "goal": "update workflow",
                "tasks": [
                    {
                        "id": "workflow",
                        "task": "update CI",
                        "write_scope": ["./.github/workflows/"],
                    }
                ],
            }
        )

        self.assertEqual(plan.tasks[0].write_scope, [".github/workflows/"])

    def test_real_agentloop_workers_use_isolated_worktrees_and_merge_disjoint_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            run_dir = root / "run"
            trace = TraceRecorder(str(run_dir / "trace.json"))
            created_llms = []

            def llm_factory():
                llm = EditingLLM()
                created_llms.append(llm)
                return llm

            coordinator = build_live_fanout(
                plan=_plan(),
                base_config=RuntimeConfig(workspace=str(repo), max_steps=3),
                trace=trace,
                run_dir=run_dir,
                llm_factory=llm_factory,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=2,
            )

            summary = coordinator.run()

            self.assertEqual(summary.status, "passed")
            self.assertEqual(summary.batches, [["alpha", "beta"]])
            self.assertEqual(set(summary.merged_task_ids), {"alpha", "beta"})
            self.assertEqual((repo / "a.py").read_text(encoding="utf-8"), "value = 'alpha'\n")
            self.assertEqual((repo / "b.py").read_text(encoding="utf-8"), "value = 'beta'\n")
            self.assertGreaterEqual(len(created_llms), 3)  # two workers plus verifier
            worker_roots = {result.workspace for result in summary.results}
            self.assertEqual(len(worker_roots), 2)
            self.assertTrue(all(not Path(path).exists() for path in worker_roots))
            for result in summary.results:
                self.assertTrue(Path(result.trace_path).exists())
                self.assertTrue(Path(result.patch_path).exists())
                self.assertTrue(Path(result.environment_manifest_path).exists())
            self.assertTrue((run_dir / "fanout" / "fanout_summary.json").exists())
            self.assertTrue((run_dir / "fanout" / "fanout_report.md").exists())
            self.assertTrue((run_dir / "fanout" / "integration.patch").exists())
            self.assertTrue((run_dir / "fanout" / "fanout_plan.json").exists())
            self.assertTrue((run_dir / "fanout" / "fanout_checkpoint.json").exists())
            self.assertTrue(Path(summary.finalizer_trace_path).exists())
            self.assertTrue(Path(summary.finalizer_usage_path).exists())
            self.assertEqual(summary.metrics["max_workers"], 2)
            report = (run_dir / "fanout" / "fanout_report.md").read_text(encoding="utf-8")
            self.assertIn("## Current Run Metrics", report)
            self.assertIn("## Recovery Accounting", report)
            self.assertIn("evidence_chain_total_tokens", report)
            self.assertNotIn("resumed_failed_tool_calls", report)
            finalizer_manifest = json.loads(
                (run_dir / "fanout" / "finalizer" / "execution_environment.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(finalizer_manifest["probe"]["mode"], "worktree")
            self.assertEqual(finalizer_manifest["cleanup_policy"], "remove")
            self.assertFalse(Path(finalizer_manifest["probe"]["active_workspace"]).exists())

    def test_scope_escape_fails_closed_without_merging_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            plan = FanoutPlan.from_mapping(
                {
                    "goal": "scope test",
                    "tasks": [
                        {
                            "id": "alpha",
                            "task": "implement alpha",
                            "write_scope": ["a.py"],
                            "allowed_tools": ["apply_patch"],
                        }
                    ],
                }
            )
            coordinator = build_live_fanout(
                plan=plan,
                base_config=RuntimeConfig(workspace=str(repo), max_steps=3),
                trace=TraceRecorder(str(root / "run" / "trace.json")),
                run_dir=root / "run",
                llm_factory=lambda: EditingLLM(escape_scope=True),
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=1,
            )

            summary = coordinator.run()

            self.assertEqual(summary.status, "conflict_resolution_required")
            self.assertEqual(summary.results[0].status, "scope_violation")
            self.assertEqual((repo / "b.py").read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual(summary.merged_task_ids, [])

    def test_write_fanout_rejects_non_recoverable_per_operation_manual_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            coordinator = build_live_fanout(
                plan=_plan(),
                base_config=RuntimeConfig(
                    workspace=str(repo),
                    max_steps=3,
                    auto_approve_writes=False,
                ),
                trace=TraceRecorder(str(root / "run" / "trace.json")),
                run_dir=root / "run",
                llm_factory=EditingLLM,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=False, execution_environment=environment
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "manual write approval"):
                coordinator.run()

    def test_new_file_is_scoped_merged_and_included_in_integration_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            plan = FanoutPlan.from_mapping(
                {
                    "goal": "create one module",
                    "tasks": [
                        {
                            "id": "alpha",
                            "task": "create new.py",
                            "write_scope": ["new.py"],
                            "allowed_tools": ["write_file", "git_diff"],
                        }
                    ],
                }
            )
            run_dir = root / "run"
            summary = build_live_fanout(
                plan=plan,
                base_config=RuntimeConfig(workspace=str(repo), max_steps=3),
                trace=TraceRecorder(str(run_dir / "trace.json")),
                run_dir=run_dir,
                llm_factory=lambda: EditingLLM(create_new=True),
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=1,
            ).run()

            self.assertEqual(summary.status, "passed")
            self.assertEqual(summary.results[0].touched_files, ["new.py"])
            self.assertEqual((repo / "new.py").read_text(encoding="utf-8"), "value = 'new'\n")
            integration_patch = Path(summary.integration_patch_path).read_text(encoding="utf-8")
            self.assertIn("new file mode", integration_patch)
            self.assertIn("b/new.py", integration_patch)

    def test_failed_dependency_is_skipped_while_independent_patch_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            plan = FanoutPlan.from_mapping(
                {
                    "goal": "dependency test",
                    "tasks": [
                        {"id": "alpha", "task": "implement alpha", "write_scope": ["a.py"]},
                        {
                            "id": "beta",
                            "task": "implement beta",
                            "depends_on": ["alpha"],
                            "write_scope": ["b.py"],
                        },
                    ],
                }
            )
            coordinator = build_live_fanout(
                plan=plan,
                base_config=RuntimeConfig(workspace=str(repo), max_steps=2),
                trace=TraceRecorder(str(root / "run" / "trace.json")),
                run_dir=root / "run",
                llm_factory=lambda: EditingLLM(fail_task="alpha"),
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=2,
            )

            summary = coordinator.run()

            statuses = {result.task_id: result.status for result in summary.results}
            self.assertEqual(summary.status, "partial_failure")
            self.assertEqual(statuses["alpha"], "blocked")
            self.assertEqual(statuses["beta"], "blocked_dependency")
            self.assertEqual((repo / "a.py").read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual((repo / "b.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_resume_reapplies_completed_patch_and_only_reruns_incomplete_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_repo = root / "first-repo"
            first_repo.mkdir()
            _init_repo(first_repo)
            first_run = root / "first-run"
            first = build_live_fanout(
                plan=_plan(),
                base_config=RuntimeConfig(workspace=str(first_repo), max_steps=3),
                trace=TraceRecorder(str(first_run / "trace.json")),
                run_dir=first_run,
                llm_factory=lambda: EditingLLM(fail_task="beta"),
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=2,
            ).run()
            self.assertEqual(first.status, "partial_failure")
            self.assertEqual(first.merged_task_ids, ["alpha"])
            (first_run / "fanout" / "fanout_summary.json").unlink()

            resumed_repo = root / "resumed-repo"
            subprocess.run(
                ["git", "clone", str(first_repo), str(resumed_repo)],
                check=True,
                capture_output=True,
            )
            second_run = root / "second-run"
            created_llms = []

            def llm_factory():
                llm = EditingLLM()
                created_llms.append(llm)
                return llm

            resumed = build_live_fanout(
                plan=_plan(),
                base_config=RuntimeConfig(workspace=str(resumed_repo), max_steps=3),
                trace=TraceRecorder(str(second_run / "trace.json")),
                run_dir=second_run,
                llm_factory=llm_factory,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=2,
                resume_from=first_run,
            ).run()

            by_id = {result.task_id: result for result in resumed.results}
            self.assertEqual(resumed.status, "passed")
            self.assertTrue(by_id["alpha"].resumed)
            self.assertFalse(by_id["beta"].resumed)
            self.assertEqual(len(created_llms), 2)  # beta plus final verifier; alpha was recovered
            self.assertEqual((resumed_repo / "a.py").read_text(encoding="utf-8"), "value = 'alpha'\n")
            self.assertEqual((resumed_repo / "b.py").read_text(encoding="utf-8"), "value = 'beta'\n")
            self.assertEqual(resumed.metrics["resumed_count"], 1)
            self.assertEqual(
                resumed.metrics["resumed_worker_duration_ms"],
                by_id["alpha"].duration_ms,
            )
            self.assertEqual(
                resumed.metrics["current_worker_duration_ms"],
                by_id["beta"].duration_ms,
            )
            self.assertEqual(
                resumed.metrics["worker_time_to_wall_ratio"],
                round(by_id["beta"].duration_ms / resumed.wall_time_ms, 4),
            )
            current_llm_calls = (
                int(by_id["beta"].usage_summary["llm_calls"])
                + int(resumed.finalizer_usage_summary["llm_calls"])
            )
            self.assertEqual(resumed.metrics["llm_calls"], current_llm_calls)
            self.assertEqual(
                resumed.metrics["resumed_llm_calls"],
                int(by_id["alpha"].usage_summary["llm_calls"]),
            )
            self.assertEqual(
                resumed.metrics["evidence_chain_llm_calls"],
                resumed.metrics["llm_calls"] + resumed.metrics["resumed_llm_calls"],
            )

    def test_fanout_resume_reuses_a_durable_worker_human_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            human_root = root / "human-input"
            first_repo = root / "first-repo"
            first_repo.mkdir()
            _init_repo(first_repo)
            plan = FanoutPlan.from_mapping(
                {
                    "goal": "apply an operator-selected compatibility change",
                    "tasks": [
                        {
                            "id": "alpha",
                            "task": "update a.py after obtaining the compatibility choice",
                            "write_scope": ["a.py"],
                            "allowed_tools": ["ask_human", "apply_patch", "git_diff"],
                        }
                    ],
                }
            )
            first_run = root / "first-run"
            first = build_live_fanout(
                plan=plan,
                base_config=RuntimeConfig(
                    workspace=str(first_repo),
                    max_steps=4,
                    human_input_root=str(human_root),
                ),
                trace=TraceRecorder(str(first_run / "trace.json")),
                run_dir=first_run,
                llm_factory=AskThenEditLLM,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=1,
            ).run()
            self.assertEqual(first.status, "partial_failure")
            self.assertEqual(first.results[0].status, "waiting_human")
            request = JsonHumanInputRepository(human_root).list_pending()[0]
            JsonHumanInputRepository(human_root).respond(request.request_id, "yes")

            resumed_repo = root / "resumed-repo"
            subprocess.run(
                ["git", "clone", str(first_repo), str(resumed_repo)],
                check=True,
                capture_output=True,
            )
            second_run = root / "second-run"
            resumed = build_live_fanout(
                plan=plan,
                base_config=RuntimeConfig(
                    workspace=str(resumed_repo),
                    max_steps=4,
                    human_input_root=str(human_root),
                ),
                trace=TraceRecorder(str(second_run / "trace.json")),
                run_dir=second_run,
                llm_factory=AskThenEditLLM,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=1,
                resume_from=first_run,
            ).run()

            self.assertEqual(resumed.status, "passed")
            self.assertEqual((resumed_repo / "a.py").read_text(encoding="utf-8"), "value = 'approved'\n")
            self.assertEqual(JsonHumanInputRepository(human_root).list_pending(), [])

    def test_resume_rejects_a_different_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            base_head = _init_repo(repo)
            previous_run = root / "previous-run" / "fanout"
            previous_run.mkdir(parents=True)
            (previous_run / "fanout_summary.json").write_text(
                json.dumps(
                    {
                        "plan_digest": "different",
                        "base_head": base_head,
                        "results": [],
                        "merged_task_ids": [],
                    }
                ),
                encoding="utf-8",
            )
            coordinator = build_live_fanout(
                plan=_plan(),
                base_config=RuntimeConfig(workspace=str(repo), max_steps=3),
                trace=TraceRecorder(str(root / "run" / "trace.json")),
                run_dir=root / "run",
                llm_factory=EditingLLM,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                resume_from=previous_run.parent,
            )

            with self.assertRaisesRegex(RuntimeError, "plan digest"):
                coordinator.run()

    def test_resume_rejects_a_tampered_completed_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            first_run = root / "first-run"
            first = build_live_fanout(
                plan=_plan(),
                base_config=RuntimeConfig(workspace=str(repo), max_steps=3),
                trace=TraceRecorder(str(first_run / "trace.json")),
                run_dir=first_run,
                llm_factory=EditingLLM,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                max_workers=2,
            ).run()
            self.assertEqual(first.status, "passed")
            beta = next(result for result in first.results if result.task_id == "beta")
            Path(beta.patch_path).write_text("tampered\n", encoding="utf-8")

            resumed_repo = root / "resumed-repo"
            subprocess.run(["git", "clone", str(repo), str(resumed_repo)], check=True, capture_output=True)
            coordinator = build_live_fanout(
                plan=_plan(),
                base_config=RuntimeConfig(workspace=str(resumed_repo), max_steps=3),
                trace=TraceRecorder(str(root / "second-run" / "trace.json")),
                run_dir=root / "second-run",
                llm_factory=EditingLLM,
                registry_factory=lambda workspace, environment: build_registry(
                    str(workspace), auto=True, execution_environment=environment
                ),
                resume_from=first_run,
            )

            with self.assertRaisesRegex(RuntimeError, "patch digest"):
                coordinator.run()
            self.assertEqual((resumed_repo / "a.py").read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual((resumed_repo / "b.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_public_run_entrypoint_routes_fanout_and_writes_candidate_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(_plan().to_dict()), encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "run",
                    "Update two independent modules",
                    "--workspace",
                    str(repo),
                    "--provider",
                    "openai-compatible",
                    "--base-url",
                    "http://fixture.invalid",
                    "--api-key",
                    "fixture-key",
                    "--model",
                    "fixture-model",
                    "--agent-mode",
                    "fanout",
                    "--fanout-plan",
                    str(plan_path),
                    "--max-workers",
                    "2",
                    "--output-root",
                    str(repo / ".agent_forge" / "runs"),
                ]
            )

            with patch(
                "agent_forge.cli.repository.build_llm",
                side_effect=lambda _config: EditingLLM(),
            ):
                run_dir = run_repository_task(args)

            summary = json.loads((run_dir / "fanout" / "fanout_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(set(summary["merged_task_ids"]), {"alpha", "beta"})
            self.assertIn("diff --git", (run_dir / "patch.diff").read_text(encoding="utf-8"))
            self.assertNotIn(".agent_forge", (run_dir / "patch.diff").read_text(encoding="utf-8"))
            self.assertIn("candidate artifact", (run_dir / "final_answer.txt").read_text(encoding="utf-8"))

    def test_summary_is_machine_readable(self):
        plan = _plan()
        payload = json.loads(json.dumps(plan.to_dict()))
        self.assertEqual(payload["goal"], "Update two independent modules")
        self.assertEqual([task["id"] for task in payload["tasks"]], ["alpha", "beta"])

    def test_finalizer_quotes_safety_evidence_without_triggering_user_input_guardrail(self):
        prompt = _finalizer_task(
            "Audit safety behavior",
            [
                LiveSubagentResult(
                    task_id="safety",
                    status="completed",
                    final_answer=(
                        "The policy blocks rm -rf, ../ escape, .env, id_rsa, "
                        "http://example.invalid and 删除 operations."
                    ),
                    artifact_path="/tmp/safety.md",
                )
            ],
        )

        self.assertTrue(input_guardrail(prompt).passed)
        self.assertIn("quoted-risk", prompt)


if __name__ == "__main__":
    unittest.main()
