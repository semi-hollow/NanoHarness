# Scripts

This folder is for project setup and deterministic verification.

```bash
scripts/setup_macos_local.sh
```

Creates/reuses `.venv`, installs the package in editable mode, and runs
`scripts/verify.sh` on macOS.

```bash
scripts/setup_wsl_local.sh
```

Same idea for Windows WSL/Ubuntu. It stays offline after installation and uses
MockLLM during verification.

```bash
scripts/verify.sh
```

Runs syntax compilation, `forge doctor`, and one MockLLM smoke run. It is safe
on company machines and does not consume DeepSeek quota. It is not the effect
benchmark; use `forge bench swebench ...` for that.

```bash
scripts/verify_mcp.sh
```

Starts the built-in stdio MCP server, discovers `forge.*` tools, calls the
offline `web_search` tool, and verifies `mcp_tools.example.json` can register
the server into `ToolRegistry`. It does not use the network unless you set
`AGENT_FORGE_MCP_ALLOW_NETWORK=1` yourself.
