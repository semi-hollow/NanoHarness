# Limitations

Agent Forge V2 is intentionally small. These limitations are part of the design and make the project honest in interviews.

- It is not a production IDE agent.
- MockLLM is deterministic and does not capture all real model failure modes.
- OpenAI-compatible client is minimal and standard-library-only.
- MCP-style adapter is not full MCP protocol.
- Symbol search uses Python AST, not a real LSP server.
- Context summarization is length-based, not semantic compression.
- Tool schema validation is lightweight, not full JSON Schema.
- Eval benchmark is a local smoke/regression suite, not large-scale benchmark.
- Trace JSON is local observability, not a distributed telemetry backend.
- Multi-agent mode is a compact handoff demo, not autonomous swarm coordination.
