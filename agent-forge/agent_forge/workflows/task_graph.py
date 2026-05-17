from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class TaskStatus(Enum):
    """Lifecycle state for one scheduled task node."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskNode:
    """One schedulable unit in a production-style agent task graph."""

    node_id: str
    agent_name: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: object | None = None
    error: str = ""


@dataclass
class TaskGraph:
    """Small DAG used by SupervisorAgent before a full orchestration framework.

    The scheduler is currently sequential for deterministic tests, but the data
    model is DAG-shaped so it can grow into parallel ready-node execution.
    """

    nodes: dict[str, TaskNode] = field(default_factory=dict)

    def add(self, node: TaskNode) -> None:
        """Add a node and reject duplicate ids early."""

        if node.node_id in self.nodes:
            raise ValueError(f"duplicate task node: {node.node_id}")
        self.nodes[node.node_id] = node

    def ready_nodes(self) -> list[TaskNode]:
        """Return pending nodes whose dependencies already passed."""

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
    """Deterministic DAG scheduler for runtime-backed subagents.

    It runs ready nodes sequentially today. That is enough to prove dependency
    tracking, retry insertion, and structured task state while keeping the demo
    readable. Parallel execution can later replace the inner loop.
    """

    def __init__(self, graph: TaskGraph, executors: dict[str, Callable[[TaskNode], object]]):
        """Map agent names to executor callbacks."""

        self.graph = graph
        self.executors = executors

    def run(self) -> list[TaskNode]:
        """Run ready nodes until the graph completes or is blocked."""

        completed = []
        while self.graph.has_pending():
            ready = self.graph.ready_nodes()
            if not ready:
                for node in self.graph.nodes.values():
                    if node.status == TaskStatus.PENDING:
                        node.status = TaskStatus.SKIPPED
                        node.error = "dependencies did not pass"
                break
            for node in ready:
                node.status = TaskStatus.RUNNING
                executor = self.executors.get(node.agent_name)
                if executor is None:
                    node.status = TaskStatus.FAILED
                    node.error = f"missing executor for {node.agent_name}"
                else:
                    try:
                        node.result = executor(node)
                        node.status = TaskStatus.PASSED if getattr(node.result, "success", True) else TaskStatus.FAILED
                    except Exception as exc:
                        node.status = TaskStatus.FAILED
                        node.error = str(exc)
                completed.append(node)
        return completed
