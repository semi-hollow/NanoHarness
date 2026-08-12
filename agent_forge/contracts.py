from __future__ import annotations

from typing import Any, Final, TypeAlias


# Public API、Runtime、CLI 与 Benchmark 共用的默认运行预算。
# 放在无外层依赖的契约模块，避免 Domain 为复用默认值反向依赖 Runtime。
DEFAULT_MAX_STEPS: Final = 32
DEFAULT_MAX_CONTEXT_CHARS: Final = 12_000
DEFAULT_MAX_PROMPT_TOKENS: Final = 65_536
DEFAULT_TIMEOUT_SECONDS: Final = 900.0
DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS: Final = 120


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
