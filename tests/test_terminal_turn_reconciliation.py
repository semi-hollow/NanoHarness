from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from agent_forge.runtime.adapters.task_state_json import JsonTaskStateRepository
from agent_forge.runtime.adapters.thread_json import JsonConversationThreadRepository
from agent_forge.runtime.domain.task import (
    RESUMABLE_RUN_STATUSES,
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.domain.thread import (
    ConversationItemDraft,
    ConversationThread,
    ThreadRun,
    Turn,
)


class TerminalTurnCrashReconciliationTest(unittest.TestCase):
    def test_restart_reconciles_terminal_checkpoint_when_finish_turn_never_ran(
        self,
    ) -> None:
        """模拟 checkpoint 已 fsync、进程却在 finish_turn 前退出的真实磁盘状态。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            thread_root = root / "threads"
            task_states = JsonTaskStateRepository(root / "task_state")
            repository = JsonConversationThreadRepository(thread_root)
            now = time.time()
            thread_id = "thread-crash"
            turn_id = "turn-crash"
            run_id = "run-crash"
            checkpoint_path = task_states.path_for(run_id)

            repository.create(
                ConversationThread(
                    thread_id=thread_id,
                    title="Crash recovery",
                    initial_task="Finish once",
                    workspace=str(root),
                    created_at=now,
                    updated_at=now,
                )
            )
            checkpoint = task_states.start(
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
                    root_task="Finish once",
                    input_item_id=f"user:{turn_id}",
                    status=TaskRunStatus.RUNNING.value,
                    created_at=now,
                    updated_at=now,
                ),
                ConversationItemDraft(
                    item_id=f"user:{turn_id}",
                    turn_id=turn_id,
                    run_id=run_id,
                    role="user",
                    content="Finish once",
                    origin="human",
                    human_authority=True,
                ),
                ThreadRun(
                    run_id=run_id,
                    artifact_dir=str(root / "runs" / run_id),
                    checkpoint_path=str(checkpoint_path),
                    status=TaskRunStatus.CREATED.value,
                    relationship="initial",
                    created_at=now,
                    updated_at=now,
                ),
            )
            follow_up_checkpoint = task_states.start(
                TaskStartRequest(
                    run_id="run-follow-up",
                    thread_id=thread_id,
                    turn_id="turn-follow-up",
                    workspace=str(root),
                    execution_workspace=str(root),
                    execution_mode="local",
                    agent_name="CodingAgent",
                )
            )
            # 与 RunLifecycle 的提交顺序一致，但故意不调用 finish_turn：
            # terminal intent -> terminal checkpoint -> <process crash>。
            repository.prepare_turn_terminal(
                thread_id,
                turn_id,
                run_id=run_id,
                status=TaskRunStatus.COMPLETED.value,
            )
            task_states.update(
                checkpoint,
                TaskCheckpointUpdate(
                    status=TaskRunStatus.COMPLETED,
                    stop_reason="completed",
                    stop_output="done",
                    final_answer="done",
                    current_step=3,
                ),
            )

            raw_before_restart = json.loads(
                (thread_root / thread_id / "thread.json").read_text(encoding="utf-8")
            )
            self.assertEqual(raw_before_restart["active_turn_id"], turn_id)
            self.assertEqual(
                json.loads(checkpoint_path.read_text(encoding="utf-8"))["status"],
                TaskRunStatus.COMPLETED.value,
            )

            # 新进程只需加载 Thread；repository 在同一锁内核对 intent + canonical
            # v4 checkpoint，并幂等完成 ThreadRun/Turn 的最后一次原子元数据写入。
            restarted = JsonConversationThreadRepository(thread_root)
            recovered = restarted.get(thread_id)
            self.assertIsNotNone(recovered)
            assert recovered is not None
            recovered_turn = recovered.require_turn(turn_id)
            self.assertEqual(recovered.active_turn_id, "")
            self.assertEqual(recovered_turn.status, TaskRunStatus.COMPLETED.value)
            self.assertEqual(recovered_turn.current_run_id, run_id)
            self.assertEqual(recovered_turn.runs[-1].status, TaskRunStatus.COMPLETED.value)
            self.assertEqual(recovered_turn.runs[-1].current_step, 3)
            self.assertNotIn(checkpoint.status, RESUMABLE_RUN_STATUSES)

            # 再次加载不会制造第二次状态变化；同一 Thread 已可接受正常 follow-up。
            self.assertEqual(restarted.get(thread_id), recovered)
            follow_up = Turn(
                turn_id="turn-follow-up",
                root_task="Continue with a new request",
                input_item_id="user:turn-follow-up",
                status=TaskRunStatus.RUNNING.value,
                created_at=now + 1,
                updated_at=now + 1,
            )
            follow_up_run = ThreadRun(
                run_id="run-follow-up",
                artifact_dir=str(root / "runs" / "run-follow-up"),
                checkpoint_path=str(task_states.path_for(follow_up_checkpoint.run_id)),
                status=TaskRunStatus.CREATED.value,
                relationship="follow_up",
                parent_run_id=run_id,
                created_at=now + 1,
                updated_at=now + 1,
            )
            continued, _ = restarted.start_turn(
                thread_id,
                follow_up,
                ConversationItemDraft(
                    item_id=follow_up.input_item_id,
                    turn_id=follow_up.turn_id,
                    run_id=follow_up_run.run_id,
                    role="user",
                    content=follow_up.root_task,
                    origin="human",
                    human_authority=True,
                ),
                follow_up_run,
            )
            self.assertEqual(continued.active_turn_id, follow_up.turn_id)


if __name__ == "__main__":
    unittest.main()
