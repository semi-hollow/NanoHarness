import unittest

from agent_forge.multi_agent.fanout import SubagentTask, build_conflict_free_batches, run_fanout


class SubagentFanoutTest(unittest.TestCase):
    def test_conflicting_ready_tasks_are_partitioned_into_serial_batches(self):
        tasks = [
            SubagentTask(id="runtime-a", task="edit A", write_scope=["agent_forge/runtime/"]),
            SubagentTask(id="runtime-b", task="edit B", write_scope=["agent_forge/runtime/agent_loop.py"]),
            SubagentTask(id="docs", task="read docs", write_scope=[]),
        ]

        batches = build_conflict_free_batches(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["runtime-a", "docs"], ["runtime-b"]])

    def test_independent_tasks_run_in_one_parallel_batch(self):
        tasks = [
            SubagentTask(id="docs", task="update docs", write_scope=["docs/"]),
            SubagentTask(id="tests", task="add tests", write_scope=["tests/"]),
        ]
        seen = []

        def runner(task):
            seen.append(task.id)
            return {"status": "completed", "touched_files": list(task.write_scope)}

        result = run_fanout(tasks, runner, max_workers=2)

        self.assertEqual(result.status, "completed")
        self.assertEqual([[task.id for task in batch] for batch in result.batches], [["docs", "tests"]])
        self.assertEqual(set(seen), {"docs", "tests"})

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

        result = run_fanout(tasks, lambda task: {"status": "completed"}, max_workers=2)

        self.assertEqual(result.status, "completed")
        self.assertEqual([[task.id for task in batch] for batch in result.batches], [["implement"], ["docs"]])

    def test_same_batch_write_scope_overlap_requires_conflict_resolution(self):
        tasks = [
            SubagentTask(id="runtime-a", task="edit runtime A", write_scope=["agent_forge/runtime/"]),
            SubagentTask(id="runtime-b", task="edit runtime B", write_scope=["agent_forge/runtime/agent_loop.py"]),
        ]
        seen = []

        result = run_fanout(tasks, lambda task: seen.append(task.id), max_workers=2)

        self.assertEqual(result.status, "conflict_resolution_required")
        self.assertEqual(seen, [])
        self.assertEqual(result.conflicts[0].task_ids, ["runtime-a", "runtime-b"])
        self.assertIn("agent_forge/runtime/", result.conflicts[0].reason)


if __name__ == "__main__":
    unittest.main()
