"""Fanout 最小任务、HARD 图校验和文件冲突规则。

系统角色：提供不依赖模型、线程、Git 或持久化的确定性 Domain 规则。
折叠导航：1 Task/Conflict contract；2 HARD 图校验；3 冲突事实；4 路径判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
# region 1. Task 与 Conflict contract：Coordinator 和测试共用的最小事实
# 核心数据：一个可独立调度的子任务及其依赖、写范围和工具预算。
@dataclass(frozen=True)
class SubagentTask:
    """Fanout DAG 中的最小执行单元。

    ``id`` 是稳定任务键；``task`` 是 worker 目标；``depends_on`` 决定 HARD readiness；
    ``write_scope`` 用于运行前冲突隔离；``allowed_tools`` 限制 worker 工具；
    ``expected_artifact`` 声明交付物；``max_steps`` 是该 worker 的循环预算。
    """

    id: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    expected_artifact: str = "task_output"
    max_steps: int = 12


# 核心数据：两个或多个子任务无法安全并发/合并的明确原因。
@dataclass(frozen=True)
class FanoutConflict:
    """包含冲突任务 ID 与路径重叠或结果冲突原因。"""

    task_ids: list[str]
    reason: str

# endregion 1. Task 与 Conflict contract 结束


# region 2. HARD 图校验：拓扑层只是 validation 结果，不是 Runtime Batch
# 核心规则：按 depends_on 拓扑排序；未知依赖、重复 ID 和环直接失败。
def build_execution_batches(tasks: list[SubagentTask]) -> list[list[SubagentTask]]:
    """返回可并发执行的依赖层级，不处理写范围冲突。

    伪代码：校验唯一 ID/已知依赖 -> 反复选出依赖已满足的节点
    -> 记录拓扑层 -> 无 ready 但仍有 remaining 时判定为环。
    """

    by_id = {task.id: task for task in tasks}
    # dict 数量变少说明存在重复 Task ID，依赖图无法唯一寻址。
    if len(by_id) != len(tasks):
        raise ValueError("subagent task ids must be unique")
    unknown_dependencies = sorted(
        {dep for task in tasks for dep in task.depends_on if dep not in by_id}
    )
    # 任何未知上游都会让 Task 永远无法 ready，执行前直接拒绝。
    if unknown_dependencies:
        raise ValueError(f"unknown dependencies: {', '.join(unknown_dependencies)}")

    remaining = list(tasks)
    completed: set[str] = set()
    batches: list[list[SubagentTask]] = []
    # 每轮剥离一个拓扑层，直到所有 Task 都进入某个 batch。
    while remaining:
        ready = [task for task in remaining if set(task.depends_on).issubset(completed)]
        # 仍有 Task 却没有 ready 节点，说明 remaining 子图形成依赖环。
        if not ready:
            cycle = ", ".join(task.id for task in remaining)
            raise ValueError(f"cyclic dependencies among subagent tasks: {cycle}")
        batches.append(ready)
        ready_ids = {task.id for task in ready}
        completed |= ready_ids
        remaining = [task for task in remaining if task.id not in ready_ids]
    return batches
# endregion 2. HARD 图校验结束


# region 3. 冲突事实：运行前查 declared scope，运行后查 actual touched_files
# 核心规则：运行前检测声明写范围的父子路径或同路径重叠。
def detect_write_scope_conflicts(tasks: list[SubagentTask]) -> list[FanoutConflict]:
    """返回静态计划冲突；空列表表示这些 Task 可并发。

    对每一对 Task 只比较一次声明 scope；同路径或父子目录都视为冲突。
    """

    conflicts: list[FanoutConflict] = []
    # 外层固定左 Task，内层只扫描其后的 Task，避免重复和自比较。
    for left_index, left in enumerate(tasks):
        # 只比较右侧剩余 Task，因此每对 scope 恰好检查一次。
        for right in tasks[left_index + 1 :]:
            overlap = _first_overlap(left.write_scope, right.write_scope)
            # 只在找到第一条重叠路径时生成一条可解释冲突事实。
            if overlap:
                conflicts.append(
                    FanoutConflict(
                        [left.id, right.id],
                        f"write scopes overlap: {overlap}",
                    )
                )
    return conflicts

# endregion 3. 冲突事实结束


# region 4. 路径判断：统一处理同路径和父子目录重叠
def _first_overlap(left_paths: list[str], right_paths: list[str]) -> str:
    # 路径集合通常很小，直接两两比较并在第一处重叠时返回稳定证据。
    for left in left_paths:
        # 当前左路径与所有右路径比较，命中第一条即可解释冲突。
        for right in right_paths:
            # 同文件以及父目录/子路径关系都属于不可安全并发的重叠。
            if _paths_overlap(left, right):
                return f"{left} <-> {right}"
    return ""


def _paths_overlap(left: str, right: str) -> bool:
    left_norm = _normalize_path(left)
    right_norm = _normalize_path(right)
    # 空 scope 代表没有声明写入，不与任何路径构成冲突。
    if not left_norm or not right_norm:
        return False
    return (
        left_norm == right_norm
        or left_norm.startswith(f"{right_norm}/")
        or right_norm.startswith(f"{left_norm}/")
    )


def _normalize_path(path: str) -> str:
    return str(path or "").strip().strip("/").rstrip("/")
# endregion 4. 路径判断结束
