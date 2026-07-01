"""Tool governance package.

Why this package exists:
    Tool calling is a boundary, not a helper function. The model proposes a
    tool name and JSON arguments; this package turns that proposal into
    governed local actions or external MCP-style tool calls.

Main pieces:
    ``registry.py`` validates tool names and arguments.
    ``run_command.py`` is the highest-risk built-in tool and shows the command
    allowlist path.
    ``mcp_config.py`` and ``mcp_stdio.py`` load/discover external tools without
    changing ``AgentLoop``.
    Small files such as ``read_file.py`` or ``apply_patch.py`` are concrete
    tool implementations behind the shared registry/sandbox policy.

If removed:
    The agent could still produce text, but it could not inspect, edit, test,
    or call external capabilities in an auditable way.
"""
