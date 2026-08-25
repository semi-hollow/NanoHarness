import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from agent_forge.harness import Harness
from agent_forge.control import RunController
from agent_forge.harness_contracts import HarnessConfig, HarnessExtensions
from agent_forge.runtime.application.context_budget import partition_context_budgets
from agent_forge.runtime.adapters import (
    JsonConversationThreadRepository,
    JsonTaskStateRepository,
)
from agent_forge.runtime.adapters.openai_compatible import AgentResponse
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.task import (
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.domain.thread import (
    ConversationItemDraft,
    ConversationThread,
    ThreadContextState,
    ThreadRun,
    Turn,
    StableTurnContextSnapshot,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry
from tests.support import StaticResponseModel


class TwoReadsThenFinalLLM:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def chat(self, messages, tools):
        self.calls += 1
        self.requests.append(list(messages))
        if self.calls == 1:
            return AgentResponse(
                None,
                [ToolCall("read-a", "read_file", {"path": "a.txt"})],
            )
        if self.calls == 2:
            return AgentResponse(
                None,
                [ToolCall("read-b", "read_file", {"path": "b.txt"})],
            )
        return AgentResponse("PASS\ncontinued after compaction", [])


class RaiseAfterResumeClaimRepository(JsonConversationThreadRepository):
    """故障注入：resume ownership 已 durable，但调用方仍收到写入异常。"""

    def claim_resume_run(self, *args, **kwargs):
        super().claim_resume_run(*args, **kwargs)
        raise OSError("fault injection: error after durable resume claim")


def _seed_resumable_turn(
    root: Path,
    *,
    run_id: str,
    task: str,
    source_hash: str,
    include_snapshot: bool = True,
    base_tool_schemas: tuple[dict[str, object], ...] = (),
    max_context_chars: int = 12_000,
    max_prompt_tokens: int = 65_536,
    reserved_output_tokens: int = 4_096,
) -> tuple[Path, Path]:
    """Build a canonical v4 checkpoint plus its authoritative Thread state."""

    thread_id = "thread-resume"
    turn_id = "turn-resume"
    threads_root = root / "threads"
    repository = JsonConversationThreadRepository(threads_root)
    now = time.time()
    repository.create(
        ConversationThread(
            thread_id=thread_id,
            title="resume fixture",
            initial_task=task,
            workspace=str(root),
            created_at=now,
            updated_at=now,
        )
    )
    checkpoint_path = root / "old_state" / f"{run_id}.json"
    checkpoint_store = JsonTaskStateRepository(root / "old_state")
    checkpoint = checkpoint_store.start(
        TaskStartRequest(
            run_id=run_id,
            thread_id=thread_id,
            turn_id=turn_id,
            workspace=str(root),
            execution_workspace=str(root),
            execution_mode="local",
            agent_name="CodingAgent",
        )
    )
    repository.start_turn(
        thread_id,
        Turn(
            turn_id=turn_id,
            root_task=task,
            input_item_id=f"user:{turn_id}",
            status="active",
            created_at=now,
            updated_at=now,
        ),
        ConversationItemDraft(
            item_id=f"user:{turn_id}",
            turn_id=turn_id,
            run_id=run_id,
            role="user",
            content=task,
            origin="human",
            human_authority=True,
        ),
        ThreadRun(
            run_id=run_id,
            artifact_dir=str(root / "old-run"),
            checkpoint_path=str(checkpoint_path),
            status=TaskRunStatus.CREATED.value,
            relationship="initial",
            created_at=now,
            updated_at=now,
        ),
    )
    for index in range(2, 8):
        repository.append(
            thread_id,
            ConversationItemDraft(
                item_id=f"historical:{index}",
                turn_id=turn_id,
                run_id=run_id,
                role="assistant",
                content=f"historical message {index}",
                origin="model",
                human_authority=False,
            ),
        )
    state = repository.save_context_state(
        ThreadContextState(
            thread_id=thread_id,
            covered_sequence=7,
            conversation_history_digest={
                "schema_version": 3,
                "authority_turn_id": turn_id,
                "covered_message_count": 7,
                "source_hash": source_hash,
                "authority_updates": ["keep the public API"],
                "resource_hints": [],
                "state_evidence": [],
                "recent_tool_transactions": [],
                "estimated_tokens_before": 1_200,
                "estimated_tokens_after": 600,
                "workspace_mutation_observed": False,
            },
        ),
        expected_revision=0,
    )
    if include_snapshot:
        stable_budget, dynamic_budget = partition_context_budgets(max_context_chars)
        state = repository.save_stable_turn_snapshot(
            thread_id,
            StableTurnContextSnapshot(
                turn_id=turn_id,
                root_task=task,
                stable_system_prefix="stable resume fixture",
                base_tool_schemas=base_tool_schemas,
                stable_context_evidence={
                    "runtime_contract": {
                        "revision": 1,
                        "model_capabilities": ModelCapabilities(
                            context_window=max(1_024, max_prompt_tokens),
                            source="legacy_model_port_default",
                        ).to_dict(),
                        "system_prompt_profile": "single_agent",
                        "stable_context_chars": stable_budget,
                        "dynamic_context_chars": dynamic_budget,
                        "max_prompt_tokens": max_prompt_tokens,
                        "reserved_output_tokens": reserved_output_tokens,
                    }
                },
            ),
            expected_revision=state.revision,
        )
    checkpoint_store.update(
        checkpoint,
        TaskCheckpointUpdate(
            status=TaskRunStatus.PAUSED,
            context_revision=state.revision,
            current_step=0,
            last_tool="replace_text",
            last_observation="old text not found",
            stop_reason="operator_pause",
            resume_hint="Re-read the file and repair the patch anchor.",
        ),
    )
    repository.record_run(
        thread_id,
        turn_id,
        ThreadRun(
            run_id=run_id,
            artifact_dir=str(root / "old-run"),
            checkpoint_path=str(checkpoint_path),
            status=TaskRunStatus.PAUSED.value,
            relationship="initial",
            stop_reason="operator_pause",
            created_at=now,
            updated_at=time.time(),
        ),
    )
    return checkpoint_path, threads_root


def _harness_config(root: Path, threads_root: Path, **overrides) -> HarnessConfig:
    values = {
        "workspace": str(root),
        "output_root": str(root / "runs"),
        "conversation_thread_root": str(threads_root),
        "approval_root": str(root / "approvals"),
        "human_input_root": str(root / "human_input"),
        "operation_ledger_root": str(root / "operation_ledger"),
        "memory_root": str(root / "memory"),
        "max_steps": 2,
    }
    values.update(overrides)
    return HarnessConfig(**values)


class TaskResumeTest(unittest.TestCase):
    def test_resume_bootstrap_reattaches_claimed_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo,
                check=True,
            )
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            config = HarnessConfig(
                workspace=str(repo),
                output_root=str(root / "runs"),
                conversation_thread_root=str(root / "threads"),
                execution_mode="worktree",
                keep_worktree=False,
                max_steps=2,
            )
            controller = RunController()
            controller.pause("fault injection before first model step")
            paused = Harness(
                model=StaticResponseModel("unused"),
                tools=ToolRegistry(),
                config=config,
                extensions=HarnessExtensions(run_control=controller),
            ).run("pause and resume in the same worktree")
            self.assertEqual(paused.status, TaskRunStatus.PAUSED)
            execution_workspace = Path(paused.checkpoint.execution_workspace)
            self.assertTrue(execution_workspace.is_dir())
            self.assertNotEqual(execution_workspace, repo.resolve())

            completed = Harness(
                model=StaticResponseModel("resumed in the original worktree"),
                tools=ToolRegistry(),
                config=config,
            ).resume(paused.artifact_dir / "task_state" / f"{paused.run_id}.json")
            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(
                Path(completed.checkpoint.execution_workspace),
                execution_workspace,
            )

    def test_bind_error_preserves_artifact_when_resume_claim_is_already_durable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_path, threads_root = _seed_resumable_turn(
                root,
                run_id="old-run",
                task="fix original failure",
                source_hash="digest-old",
            )
            repository = RaiseAfterResumeClaimRepository(threads_root)
            harness = Harness(
                model=StaticResponseModel("unused"),
                tools=ToolRegistry(),
                config=_harness_config(root, threads_root),
                extensions=HarnessExtensions(conversation_threads=repository),
            )

            with self.assertRaisesRegex(OSError, "after durable resume claim"):
                harness.resume(checkpoint_path)

            loaded = repository.get("thread-resume")
            assert loaded is not None
            current = loaded.require_turn("turn-resume")
            self.assertNotEqual(current.current_run_id, "old-run")
            claimed = next(
                run for run in current.runs if run.run_id == current.current_run_id
            )
            self.assertTrue(Path(claimed.artifact_dir).is_dir())
            self.assertFalse(
                (root / ".agent_forge" / "internal" / "index" / "run.txt").exists()
            )

    def test_stale_resume_checkpoint_is_rejected_without_publishing_run_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_path, threads_root = _seed_resumable_turn(
                root,
                run_id="old-run",
                task="fix original failure",
                source_hash="digest-old",
            )
            repository = JsonConversationThreadRepository(threads_root)
            now = time.time()
            claimed_state = JsonTaskStateRepository(root / "already-claimed-run")
            claimed_state.start(
                TaskStartRequest(
                    run_id="already-claimed-run",
                    thread_id="thread-resume",
                    turn_id="turn-resume",
                    workspace=str(root),
                    execution_workspace=str(root),
                    execution_mode="local",
                    agent_name="CodingAgent",
                    context_revision=1,
                )
            )
            repository.claim_resume_run(
                "thread-resume",
                "turn-resume",
                expected_current_run_id="old-run",
                run=ThreadRun(
                    run_id="already-claimed-run",
                    artifact_dir=str(root / "already-claimed-run"),
                    checkpoint_path=str(
                        root / "already-claimed-run" / "already-claimed-run.json"
                    ),
                    status=TaskRunStatus.CREATED.value,
                    relationship="resume",
                    parent_run_id="old-run",
                    created_at=now,
                    updated_at=now,
                ),
            )
            custom_states = JsonTaskStateRepository(root / "custom-state")
            harness = Harness(
                model=StaticResponseModel("unused"),
                tools=ToolRegistry(),
                config=_harness_config(root, threads_root),
                extensions=HarnessExtensions(
                    checkpoint_repository=custom_states,
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "stale or concurrent"):
                harness.resume(checkpoint_path)

            runs_root = root / "runs"
            self.assertTrue(runs_root.is_dir())
            self.assertEqual(list(runs_root.iterdir()), [])
            self.assertEqual(list(custom_states.root.glob("*.json")), [])
            loaded = repository.get("thread-resume")
            assert loaded is not None
            self.assertEqual(
                loaded.require_turn("turn-resume").current_run_id,
                "already-claimed-run",
            )

    def test_resume_rejects_missing_turn_snapshot_before_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_path, threads_root = _seed_resumable_turn(
                root,
                run_id="old-run",
                task="fix original failure",
                source_hash="digest-old",
                include_snapshot=False,
            )
            repository = JsonConversationThreadRepository(threads_root)
            before_state = repository.load_context_state("thread-resume")
            assert before_state is not None
            model = StaticResponseModel("must not run")

            with self.assertRaisesRegex(
                RuntimeError,
                "without durable StableTurnContextSnapshot",
            ):
                Harness(
                    model=model,
                    tools=ToolRegistry(),
                    config=_harness_config(root, threads_root),
                ).resume(checkpoint_path)

            self.assertEqual(model.calls, 0)
            after_state = repository.load_context_state("thread-resume")
            assert after_state is not None
            self.assertEqual(after_state.revision, before_state.revision)
            self.assertIsNone(
                repository.load_stable_turn_snapshot("thread-resume", "turn-resume")
            )
            thread = repository.get("thread-resume")
            assert thread is not None
            self.assertEqual(
                thread.require_turn("turn-resume").current_run_id,
                "old-run",
            )
            runs_root = root / "runs"
            self.assertTrue(runs_root.is_dir())
            self.assertEqual(list(runs_root.iterdir()), [])

    def test_resume_rejects_incompatible_existing_turn_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_path, threads_root = _seed_resumable_turn(
                root,
                run_id="old-run",
                task="fix original failure",
                source_hash="digest-old",
            )
            model = StaticResponseModel("must not run")
            repository = JsonConversationThreadRepository(threads_root)
            before_turn = repository.get("thread-resume")
            assert before_turn is not None
            before_turn = before_turn.require_turn("turn-resume")

            with self.assertRaisesRegex(
                ValueError,
                "current Runtime is incompatible with frozen Turn context",
            ):
                Harness(
                    model=model,
                    tools=ToolRegistry(),
                    config=_harness_config(
                        root,
                        threads_root,
                        max_prompt_tokens=8_192,
                        reserved_output_tokens=2_048,
                    ),
                ).resume(checkpoint_path)

            self.assertEqual(model.calls, 0)
            after_thread = repository.get("thread-resume")
            assert after_thread is not None
            after_turn = after_thread.require_turn("turn-resume")
            self.assertEqual(after_turn.current_run_id, before_turn.current_run_id)
            self.assertEqual(after_turn.runs, before_turn.runs)
            snapshot = repository.load_stable_turn_snapshot(
                "thread-resume", "turn-resume"
            )
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.root_task, "fix original failure")
            self.assertEqual(list((root / "runs").iterdir()), [])

    def test_resume_reads_digest_from_thread_state_and_records_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint_path, threads_root = _seed_resumable_turn(
                root,
                run_id="old-run",
                task="fix original failure",
                source_hash="digest-old",
            )
            llm = StaticResponseModel("PASS\ncontinued from checkpoint")
            result = Harness(
                model=llm,
                tools=ToolRegistry(),
                config=_harness_config(root, threads_root),
            ).resume(checkpoint_path)

            self.assertIn("continued from checkpoint", result.stop_output)
            self.assertEqual(result.checkpoint.last_tool, "replace_text")
            digest_messages = [
                message
                for message in llm.messages
                if message.role == "system"
                and (message.content or "").startswith("conversation_history_digest")
            ]
            self.assertEqual(len(digest_messages), 1)
            self.assertIn("digest-old", digest_messages[0].content or "")
            self.assertIn("current_turn_authority: turn-resume", digest_messages[0].content or "")
            self.assertIn("keep the public API", digest_messages[0].content or "")
            self.assertNotIn("conversation_history_digest", result.checkpoint.to_dict())
            assert result.trace_path is not None
            trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
            resume_window = next(
                event
                for event in trace["events"]
                if event["event_type"] == "context_window"
            )
            self.assertEqual(
                resume_window["context_window"]["covered_message_count"],
                7,
            )
            self.assertEqual(
                resume_window["context_window"]["covered_delta_count"],
                0,
            )
            self.assertTrue(
                any(
                    event["event_type"] == "resume_state_loaded"
                    for event in trace["events"]
                )
            )

    def test_resume_compaction_merges_new_raw_delta_into_thread_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a" * 20_000, encoding="utf-8")
            (root / "b.txt").write_text("b" * 20_000, encoding="utf-8")
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            checkpoint_path, threads_root = _seed_resumable_turn(
                root,
                run_id="old-compacted-run",
                task="read both files and summarize",
                source_hash="digest-before-resume",
                base_tool_schemas=tuple(dict(item) for item in registry.schemas()),
                max_context_chars=12_000,
                max_prompt_tokens=4_000,
                reserved_output_tokens=500,
            )
            journal_path = threads_root / "thread-resume" / "conversation.jsonl"
            journal_before_resume = journal_path.read_bytes()
            model = TwoReadsThenFinalLLM()
            result = Harness(
                model=model,
                tools=registry,
                config=_harness_config(
                    root,
                    threads_root,
                    max_context_chars=12_000,
                    max_prompt_tokens=4_000,
                    reserved_output_tokens=500,
                    max_steps=3,
                ),
            ).resume(checkpoint_path)
            state = JsonConversationThreadRepository(threads_root).load_context_state(
                result.thread_id
            )
            assert state is not None
            repository = JsonConversationThreadRepository(threads_root)
            thread = repository.get(result.thread_id)
            assert thread is not None
            durable_items = repository.list_items(result.thread_id)

            self.assertIn("continued after compaction", result.stop_output)
            self.assertTrue(journal_path.read_bytes().startswith(journal_before_resume))
            self.assertEqual(len(durable_items), thread.sequence)
            self.assertLess(state.covered_sequence, thread.sequence)
            self.assertTrue(
                any(
                    message.role == "system"
                    and message.content.startswith("conversation_history_digest")
                    for message in model.requests[-1]
                )
            )
            self.assertTrue(
                any(message.role == "tool" for message in model.requests[-1])
            )
            self.assertGreater(
                int(state.conversation_history_digest["covered_message_count"]),
                7,
            )
            self.assertNotEqual(
                state.conversation_history_digest["source_hash"],
                "digest-before-resume",
            )
            assert result.trace_path is not None
            trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    event["event_type"] == "context_window"
                    and event["context_window"]["compacted"]
                    for event in trace["events"]
                )
            )


if __name__ == "__main__":
    unittest.main()
