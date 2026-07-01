# Scripts

This folder is for local setup, the browser workbench launcher, and deterministic
verification.

```text
scripts/start_workbench.command
```

macOS double-click launcher. It enters the repository, creates/reuses `.venv`,
installs the package when needed, and opens the local browser workbench. Daily
agent runs should be configured from that page instead of by memorizing command
flags.

```bash
scripts/setup_macos_local.sh
```

Creates/reuses `.venv`, installs the package in editable mode, and runs
`scripts/verify.sh` on macOS.

```bash
scripts/setup_wsl_local.sh
```

Same idea for Windows WSL/Ubuntu. It stays local after installation; a real
DeepSeek smoke run executes only when `DEEPSEEK_API_KEY` is set.

```bash
scripts/verify.sh
```

Runs syntax compilation, `forge doctor`, public CLI checks, unit smoke, and a
read-only real-model run when `DEEPSEEK_API_KEY` is available. It is not the
effect benchmark; use `forge bench swebench ...` for that.

```bash
scripts/verify_mcp.sh
```

Starts the built-in stdio MCP server, discovers `forge.*` tools, calls the
offline `web_search` tool, and verifies `mcp_tools.json` can register
the server into `ToolRegistry`. It does not use the network unless you set
`AGENT_FORGE_MCP_ALLOW_NETWORK=1` yourself.
