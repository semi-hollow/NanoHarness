from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .artifact import TaskArtifact


class TaskStatus(Enum):
    """Lifecycle state for one scheduled task node.

    The scheduler records status explicitly so supervisor decisions can be
    audited after the run instead of inferred from printed output.
    """

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskNode:
    """One schedulable unit in a production-style agent task graph."""

    # Unique node id inside the graph, for dependency and trace references.
    node_id: str

    # Which AgentSpec/AgentRuntime should execute this node.
    agent_name: str

    # Natural-language task payload for the worker.
    task: str

    # Node ids that must pass before this node can run.
    depends_on: list[str] = field(default_factory=list)

    # Read/write sets let the scheduler reason about safe parallelism.
    read_files: set[str] = field(default_factory=set)
    write_files: set[str] = field(default_factory=set)

    # Required artifact kinds. Missing artifacts fail the node even if prose
    # sounds successful.
    expected_artifacts: set[str] = field(default_factory=set)

    # Runtime fields filled by TaskScheduler.
    status: TaskStatus = TaskStatus.PENDING
    result: object | None = None
    error: str = ""
    artifacts: list[TaskArtifact] = field(default_factory=list)


@dataclass
class TaskGraph:
    """DAG used by SupervisorAgent for production-shaped orchestration."""

    # Map node_id -> node so dependency lookups are O(1) and trace ids are stable.
    nodes: dict[str, TaskNode] = field(default_factory=dict)

    def add(self, node: TaskNode) -> None:
        """Add a node and reject duplicate ids early."""

        if node.node_id in self.nodes:
            raise ValueError(f"duplicate task node: {node.node_id}")
        self.nodes[node.node_id] = node

    def ready_nodes(self) -> list[TaskNode]:
        """Return pending nodes whose dependencies already passed.

        Failed dependencies intentionally do not make a node ready; `run()`
        later marks blocked nodes as SKIPPED.
        """

        ready = []
        for node in self.nodes.values():
            if node.status != TaskStatus.PENDING:
                continue
            if all(self.nodes[dep].status == TaskStatus.PASSED for dep in node.depends_on):
                ready.append(node)
        return ready

    def has_pending(self) -> bool:
        """Return whether any work remains schedulable or blocked."""

        return any(node.status == TaskStatus.PENDING for node in self.nodes.values())


class TaskScheduler:
    """Conflict-aware DAG scheduler for runtime-backed subagents.

    Ready nodes with disjoint write ownership can run concurrently. Nodes that
    would write the same file are split into separate batches, which is the
    minimal production behavior needed before adding full patch merge logic.
    """

    def __init__(
        self,
        graph: TaskGraph,
        executors: dict[str, Callable[[TaskNode], object]],
        max_workers: int = 4,
    ):
        """Map agent names to executor callbacks and set parallelism."""

        self.graph = graph
        self.executors = executors
        self.max_workers = max(1, max_workers)

    def run(self) -> list[TaskNode]:
        """Run ready nodes until the graph completes or is blocked."""

        completed = []
        while self.graph.has_pending():
            ready = self.graph.ready_nodes()
            if not ready:
                # If nothing is ready but pending nodes remain, their upstream
                # dependencies failed or were skipped. Mark them explicitly.
                for node in self.graph.nodes.values():
                    if node.status == TaskStatus.PENDING:
                        node.status = TaskStatus.SKIPPED
                        node.error = "dependencies did not pass"
                break
            for batch in self._conflict_safe_batches(ready):
                completed.extend(self._run_batch(batch))
        return completed

    def _conflict_safe_batches(self, ready: list[TaskNode]) -> list[list[TaskNode]]:
        """Group ready nodes so no batch has overlapping write ownership.

        This is the lightweight version of worktree/merge isolation: independent
        readers can run together, but two writers for the same file are separated.
        """

        batches: list[list[TaskNode]] = []
        batch_writes: list[set[str]] = []
        for node in ready:
            writes = set(node.write_files)
            placed = False
            for index, existing_writes in enumerate(batch_writes):
                if writes and existing_writes.intersection(writes):
                    continue
                batches[index].append(node)
                existing_writes.update(writes)
                placed = True
                break
            if not placed:
                batches.append([node])
                batch_writes.append(set(writes))
        return batches

    def _run_batch(self, batch: list[TaskNode]) -> list[TaskNode]:
        """Run one conflict-free batch sequentially or in parallel."""

        if self.max_workers == 1 or len(batch) == 1:
            return [self._run_node(node) for node in batch]
        completed = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(batch))) as pool:
            futures = {pool.submit(self._run_node, node): node for node in batch}
            for future in as_completed(futures):
                completed.append(future.result())
        return completed

    def _run_node(self, node: TaskNode) -> TaskNode:
        """Execute one node and attach result artifacts."""

        node.status = TaskStatus.RUNNING
        executor = self.executors.get(node.agent_name)
        if executor is None:
            node.status = TaskStatus.FAILED
            node.error = f"missing executor for {node.agent_name}"
            return node
        try:
            node.result = executor(node)
            node.status = TaskStatus.PASSED if getattr(node.result, "success", True) else TaskStatus.FAILED
            node.artifacts = list(getattr(node.result, "artifacts", []) or [])
            if node.expected_artifacts:
                # Artifact validation prevents a worker from passing by writing
                # only a vague final answer without machine-readable evidence.
                produced = {artifact.kind for artifact in node.artifacts}
                missing = node.expected_artifacts - produced
                if missing:
                    node.status = TaskStatus.FAILED
                    node.error = f"missing artifacts: {', '.join(sorted(missing))}"
        except Exception as exc:
            node.status = TaskStatus.FAILED
            node.error = str(exc)
        return node
