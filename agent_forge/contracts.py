from __future__ import annotations

from typing import Any, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

ToolArguments: TypeAlias = dict[str, Any]
ToolSchema: TypeAlias = dict[str, Any]
