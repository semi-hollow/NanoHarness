# Scripts 说明

该目录存放本地环境初始化、浏览器工作台启动和确定性验证脚本。

```text
scripts/start_workbench.command
```

macOS 双击启动器。它会进入仓库、创建或复用 `.venv`、在需要时安装 package，并打开
本地浏览器工作台。日常 Agent run 建议从页面配置，不必记忆全部 command flag。

```bash
scripts/setup_macos_local.sh
```

在 macOS 上创建或复用 `.venv`，以 editable mode 安装 package，然后运行
`scripts/verify.sh`。

```bash
scripts/setup_wsl_local.sh
```

Windows WSL/Ubuntu 版本。安装后保持本地运行；只有设置 `DEEPSEEK_API_KEY` 时才执行
真实 DeepSeek smoke run。

```bash
scripts/verify.sh
```

执行 syntax compilation、`forge doctor`、public CLI check、unit smoke；存在
`DEEPSEEK_API_KEY` 时还会执行只读 real-model run。它不是效果 benchmark；效果验证
使用 `forge bench swebench ...`。

```bash
scripts/verify_mcp.sh
```

启动内置 stdio MCP server，发现 `forge.*` tool，调用 offline `web_search` tool，并
验证 `mcp_tools.json` 能将 server 注册到 `ToolRegistry`。除非手动设置
`AGENT_FORGE_MCP_ALLOW_NETWORK=1`，否则不会使用 network。
