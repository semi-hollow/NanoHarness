"""Runtime 为 Turn 稳定前缀和 Model Step 动态上下文分配独立预算。"""

from __future__ import annotations


def partition_context_budgets(total_chars: int) -> tuple[int, int]:
    """返回稳定前缀与动态仓库区各自独立、确定性的字符预算。"""

    total = max(512, int(total_chars))
    stable = max(256, total // 2)
    return stable, max(256, total - stable)
