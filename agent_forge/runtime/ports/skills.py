"""Runtime 使用 Skill capability 时需要的最小契约。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class SkillView(Protocol):
    """一次运行可见的不可变 Skill 信息。"""

    @property
    def name(self) -> str:
        """返回稳定 Skill 名称。"""

    @property
    def version(self) -> str:
        """返回本次选择的版本。"""

    @property
    def tool_names(self) -> list[str]:
        """返回该 Skill 期望使用的工具。"""

    @property
    def entrypoint(self) -> str:
        """返回实现标识。"""

    def prompt_card(self) -> str:
        """返回模型可见的 Skill 操作卡。"""


class SkillSelectorPort(Protocol):
    """按任务选择已装配 Skill 的端口。"""

    def select_for_task(
        self,
        task: str,
        *,
        names: list[str] | None = None,
        limit: int = 3,
    ) -> Sequence[SkillView]:
        """返回本次运行启用的 Skill。"""
