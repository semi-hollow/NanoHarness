"""Built-in MCP server package for Agent Forge.

The agent can already consume stdio MCP tools through
``agent_forge.tools.mcp_stdio``. This package provides the other side of that
boundary: a small project-owned MCP server with useful default tools. Keeping
the server in a separate package makes the teaching point explicit:

* ``agent_forge.tools`` is the client/registry side used by AgentLoop.
* ``agent_forge.mcp`` is an external-tool process that happens to ship in this
  repository, so it exercises the same protocol path a real remote tool server
  would use.
"""

