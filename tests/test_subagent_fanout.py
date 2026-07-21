import unittest

from agent_forge.multi_agent.domain.fanout import (
    SubagentTask,
    build_conflict_free_batches,
    build_execution_batches,
    detect_write_scope_conflicts,
)


class SubagentFanoutTest(unittest.TestCase):
    def test_conflicting_ready_tasks_are_partitioned_into_serial_batches(self):
        tasks = [
            SubagentTask(id="runtime-a", task="edit A", write_scope=["agent_forge/runtime/"]),
            SubagentTask(id="runtime-b", task="edit B", write_scope=["agent_forge/runtime/application/agent_loop.py"]),
            SubagentTask(id="docs", task="read docs", write_scope=[]),
        ]

        batches = build_conflict_free_batches(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["runtime-a", "docs"], ["runtime-b"]])

    def test_dependencies_force_later_batch(self):
        tasks = [
            SubagentTask(id="implement", task="implement feature", write_scope=["agent_forge/runtime/"]),
            SubagentTask(
                id="docs",
                task="document feature",
                depends_on=["implement"],
                write_scope=["docs/"],
            ),
        ]

        batches = build_execution_batches(tasks)

        self.assertEqual(
            [[task.id for task in batch] for batch in batches],
            [["implement"], ["docs"]],
        )

    def test_same_batch_write_scope_overlap_requires_conflict_resolution(self):
        tasks = [
            SubagentTask(id="runtime-a", task="edit runtime A", write_scope=["agent_forge/runtime/"]),
            SubagentTask(id="runtime-b", task="edit runtime B", write_scope=["agent_forge/runtime/application/agent_loop.py"]),
        ]
        conflicts = detect_write_scope_conflicts(tasks)

        self.assertEqual(conflicts[0].task_ids, ["runtime-a", "runtime-b"])
        self.assertIn("agent_forge/runtime/", conflicts[0].reason)


if __name__ == "__main__":
    unittest.main()
