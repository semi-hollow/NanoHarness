"""Shared type contracts for JSON and model-visible tool boundaries.

Keep these aliases small. Domain records belong beside their owning runtime
module; this file only names the shapes that legitimately cross many layers.
"""

from __future__ import annotations

from typing import Any, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

# LLM tool calls and tool schemas are JSON protocol objects. Naming the aliases
# keeps callers from falling back to unexplained ``dict`` parameters.
# These two objects are untrusted protocol inputs. Concrete tools and
# ToolRegistry validate fields before use; ``Any`` is deliberate at this edge,
# rather than leaking through owned runtime state.
ToolArguments: TypeAlias = dict[str, Any]
ToolSchema: TypeAlias = dict[str, Any]
