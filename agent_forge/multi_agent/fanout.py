"""兼容导入：fanout 数据和冲突规则已迁移到 domain。"""

from .domain.fanout import (
    FanoutConflict,
    FanoutResult,
    SubagentResult,
    SubagentTask,
    build_conflict_free_batches,
    build_execution_batches,
    detect_result_conflicts,
    detect_write_scope_conflicts,
)
from .application.fanout import run_fanout

__all__ = [
    "FanoutConflict",
    "FanoutResult",
    "SubagentResult",
    "SubagentTask",
    "build_conflict_free_batches",
    "build_execution_batches",
    "detect_result_conflicts",
    "detect_write_scope_conflicts",
    "run_fanout",
]
