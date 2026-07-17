"""一次 Agent run 的易失工作记忆。

这个对象属于 Runtime 的运行期状态，而不是长期记忆仓储：

``RunPreparation`` 注入 continuation 摘要和长期召回结果；
工具执行阶段追加 ``Observation``；
``ContextAssemblerPort`` 只读取 ``recent/summary/long_term`` 视图。

长期记忆生命周期由 ``context.application.memory_service`` 管理，完整会话窗口压缩由
``context.application.compaction`` 管理。三者不要混为同一个“Memory”。
"""

from __future__ import annotations

from agent_forge.context.domain import LongTermMemoryRecord
from agent_forge.runtime.domain.conversation import Observation


# 核心数据：单次 run 的最近事实、工具观察、摘要和只读长期召回结果。
class WorkingMemory:
    """一次 run 的易失 working memory，以及已召回的长期记忆视图。

    属性说明：

    - ``items``：最近的普通事实；``observations``：最近的类型化工具结果。
    - ``summaries``：移出最近窗口的有界摘要；``store``：当前 run 的显式键值。
    - ``long_term_records``：已经过隔离和权威过滤的只读长期记忆。
    - ``n``：最近事实与 Observation 的保留上限。
    """

    def __init__(self, n: int = 8) -> None:
        self.items: list[object] = []
        self.observations: list[Observation] = []
        self.summaries: list[str] = []
        self.store: dict[str, object] = {}
        self.long_term_records: list[LongTermMemoryRecord] = []
        self.n = n

    def add(self, item: object) -> None:
        self.items = (self.items + [item])[-self.n :]

    def recent(self) -> list[object]:
        return list(self.items)

    def set(self, key: str, value: object) -> None:
        """保存当前 run 内的显式键值，不冒充长期记忆。"""

        self.store[key] = value

    def seed_session(self, previous_task: str = "", session_summary: str = "") -> None:
        """在 run 开始时注入显式的前序任务和 continuation 摘要。"""

        if previous_task:
            self.set("previous_task", previous_task)
        if session_summary:
            self.summaries.append(session_summary)

    def seed_long_term(self, records: list[LongTermMemoryRecord]) -> None:
        """注入本次任务已经通过 policy 的只读召回结果。"""

        self.long_term_records = list(records)

    def long_term(self) -> list[LongTermMemoryRecord]:
        """返回隔离后的长期记忆视图。"""

        return list(self.long_term_records)

    def get(self, key: str, default: object = None) -> object:
        return self.store.get(key, default)

    def add_observation(self, observation: Observation | str) -> None:
        """保存工具结果；超过 ``n`` 时只压缩最旧一条。"""

        if isinstance(observation, Observation):
            normalized = observation
        else:
            normalized = Observation("memory", True, str(observation))
        self.observations.append(normalized)
        if len(self.observations) > self.n:
            self._compact_oldest_observation()

    def recent_observations(self) -> list[Observation]:
        return list(self.observations)

    def clear(self) -> None:
        self.items.clear()
        self.observations.clear()
        self.summaries.clear()
        self.store.clear()
        self.long_term_records.clear()

    def summary(self, max_chars: int = 800) -> str:
        """压缩 working memory；长期记忆由独立区段渲染。"""

        recent = "; ".join(str(item) for item in self.items)
        observations = "; ".join(
            f"{item.tool_name}:{'ok' if item.success else 'fail'}:{item.content[:80]}"
            for item in self.observations
        )
        values = ", ".join(f"{key}={value}" for key, value in self.store.items())
        summaries = "; ".join(self.summaries[-3:])
        text = " | ".join(
            part for part in [summaries, recent, observations, values] if part
        )
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 14] + " [compressed]"

    def _compact_oldest_observation(self) -> None:
        if not self.observations:
            return
        oldest = self.observations.pop(0)
        status = "ok" if oldest.success else "fail"
        note = f"{oldest.tool_name}:{status}:{oldest.content[:120]}"
        self.summaries = (self.summaries + [note])[-5:]
