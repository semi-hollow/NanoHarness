from __future__ import annotations

from typing import Any, Final, TypeAlias


# 所有层共用的 workspace 文件写工具名单；新增写工具时只修改这一处。
WORKSPACE_WRITE_TOOL_NAMES: Final[tuple[str, ...]] = (
    "replace_text",
    "create_file",
    "write_file",
)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

ToolArguments: TypeAlias = dict[str, Any]
ToolSchema: TypeAlias = dict[str, Any]
