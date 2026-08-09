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
        """返回该 Skill 声明的全部必需与可选工具。"""

    @property
    def required_tool_names(self) -> list[str]:
        """返回激活后必须已注册的工具；缺失时 Runtime 拒绝启动本轮。"""

    @property
    def optional_tool_names(self) -> list[str]:
        """返回有则可用、缺失也不破坏核心工作流的工具。"""

    @property
    def entrypoint(self) -> str:
        """返回实现标识。"""

    @property
    def source(self) -> str:
        """返回 Skill 定义来源。"""

    @property
    def content_sha256(self) -> str:
        """返回主指令内容身份，供 Trace 和评测固定版本。"""

    @property
    def selection_reason(self) -> str:
        """返回本次选择来自显式指定、规则命中还是有界兜底。"""

    @property
    def loaded_resources(self) -> Sequence["SkillResourceView"]:
        """返回本 Run 实际披露的有界参考资源。"""

    def prompt_card(self) -> str:
        """返回模型可见的 Skill 操作卡。"""


class SkillResourceView(Protocol):
    """Runtime 记录资源披露证据所需的最小只读视图。"""

    @property
    def path(self) -> str:
        """返回 Skill 包内相对路径。"""

    @property
    def description(self) -> str:
        """返回资源用途。"""

    @property
    def sha256(self) -> str:
        """返回完整资源正文哈希。"""

    @property
    def original_chars(self) -> int:
        """返回裁剪前字符数。"""

    @property
    def disclosed_chars(self) -> int:
        """返回实际注入字符数。"""

    @property
    def truncated(self) -> bool:
        """返回资源是否因预算裁剪。"""


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
