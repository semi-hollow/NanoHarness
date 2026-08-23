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
        "Propose action=CREATE, UPDATE, or NOOP. UPDATE/NOOP require a target_memory_id "
        "from the supplied Memory Management Catalog; CREATE must omit it. "
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
                "action": "str",
                "target_memory_id": "str",
                "key": "str",
                "content": "str",
                "scope": "str",
                "source_quote": "str",
            },
            "required": ["action", "key", "content", "source_quote"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        """收口一次 typed proposal；CREATE/UPDATE 的结果从下一 Run 起参与召回。"""

        action = str(arguments.get("action") or "").strip().upper()
        record = self._service.apply_consolidation(
            project_namespace=self._project_namespace,
            action=action,
            target_memory_id=str(arguments.get("target_memory_id") or ""),
            key=str(arguments.get("key") or ""),
            content=str(arguments.get("content") or ""),
            scope=str(
                arguments.get("scope") or MemoryScope.PROJECT.value
            ).strip(),
            source_quote=str(arguments.get("source_quote") or ""),
        )
        return Observation(
            self.name,
            True,
            (
                f"memory_consolidated: action={action} id={record.memory_id} "
                f"scope={record.scope} "
                f"key={record.key} revision={record.revision}; current Run memory "
                "snapshot is unchanged; the value is recalled by the next Run"
            ),
        )


__all__ = ["RememberMemoryTool"]
