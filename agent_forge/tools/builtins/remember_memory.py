"""用户明确授权后写入 machine-local Long-Term Memory 的内置 Tool。"""

from __future__ import annotations

from agent_forge.memory.adapters import JsonLongTermMemoryRepository
from agent_forge.memory.application import LongTermMemoryService
from agent_forge.memory.domain import MemoryScope
from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation

from agent_forge.tools.base import Tool


class RememberMemoryTool(Tool):
    """复用 LongTermMemoryService；原文授权校验由执行管线完成。"""

    name = "remember_memory"
    description = (
        "Persist information only when the user explicitly asks to remember it for "
        "future runs. source_quote must be an exact quote from that user message. "
        "Never persist model-inferred facts. Default scope is project; use user only "
        "when the quote explicitly requests a global or cross-project preference."
    )

    def __init__(
        self,
        *,
        memory_root: str,
        project_namespace: str,
    ) -> None:
        self._service = LongTermMemoryService(
            JsonLongTermMemoryRepository(memory_root)
        )
        self._project_namespace = project_namespace

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                "key": "str",
                "content": "str",
                "scope": "str",
                "source_quote": "str",
            },
            "required": ["key", "content", "source_quote"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        """校验字段后写入一条记忆，并明确它只会从下一次 Run 起参与召回。"""

        record = self._service.remember(
            project_namespace=self._project_namespace,
            key=str(arguments.get("key") or ""),
            content=str(arguments.get("content") or ""),
            scope=str(
                arguments.get("scope") or MemoryScope.PROJECT.value
            ).strip(),
        )
        return Observation(
            self.name,
            True,
            (
                f"memory_saved: id={record.memory_id} scope={record.scope} "
                f"key={record.key} revision={record.revision}; current Run memory "
                "snapshot is unchanged; the value is recalled by the next Run"
            ),
        )


__all__ = ["RememberMemoryTool"]
