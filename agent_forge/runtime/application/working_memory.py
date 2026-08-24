"""一次 Agent run 的易失工作记忆。

这个对象属于 Runtime 的运行期状态，而不是长期记忆仓储：

应用服务只追加由执行事实提炼出的 task state；
``SystemContextAssemblerPort`` 只读取 ``recent/summary`` 派生视图。

长期记忆的显式 remember/forget 由 ``memory.application`` 管理，完整会话窗口压缩由
``context.application.compaction`` 管理。原始 Tool Observation 只属于 Conversation，
不再镜像到本对象；三者不要混为同一个“Memory”。
"""

from __future__ import annotations

# 核心数据：单次执行的派生任务状态和有界摘要。
class WorkingMemory:
    """一次执行的易失、派生 task state。

    属性说明：

    - ``items``：从执行事实中显式提炼出的最近 task state。
    - ``summaries``：移出最近窗口的有界摘要；``store``：当前 run 的显式键值。
    - ``n``：最近派生事实的保留上限。
    """

    def __init__(self, n: int = 8) -> None:
        self.items: list[object] = []
        self.summaries: list[str] = []
        self.store: dict[str, object] = {}
        self.n = n

    def add(self, item: object) -> None:
        self.items = (self.items + [item])[-self.n :]

    def recent(self) -> list[object]:
        return list(self.items)

    def set(self, key: str, value: object) -> None:
        """保存当前 run 内的显式键值，不冒充长期记忆。"""

        self.store[key] = value

    def get(self, key: str, default: object = None) -> object:
        return self.store.get(key, default)

    def clear(self) -> None:
        self.items.clear()
        self.summaries.clear()
        self.store.clear()

    def summary(self, max_chars: int = 800) -> str:
        """压缩 working memory；长期记忆由独立区段渲染。"""

        recent = "; ".join(str(item) for item in self.items)
        values = ", ".join(f"{key}={value}" for key, value in self.store.items())
        summaries = "; ".join(self.summaries[-3:])
        text = " | ".join(
            part for part in [summaries, recent, values] if part
        )
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 14] + " [compressed]"
