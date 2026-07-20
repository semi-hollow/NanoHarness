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

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows_local.ps1
```

Windows 原生 PowerShell 5.1+ 版本。它优先复用 Codex bundled Python，普通终端再选择
本机 Python 3.11，创建或复用
`.venv-win\Scripts\python.exe`，先执行最小的 `pip install -e .`，再调用
`scripts/verify.ps1` 做 import 与 `forge --help` smoke。它不会读取或覆盖 macOS/WSL
使用的 `.venv`。需要 mypy 时追加 `-WithDev`；只有确实要在 Windows 跑数据集流程时才追加
`-WithBench`，避免默认安装体积较大的 benchmark 依赖。

```bash
scripts/verify.sh
```

执行 syntax compilation、`forge doctor`、public CLI check、unit smoke；存在
`DEEPSEEK_API_KEY` 时还会执行只读 real-model run。它不是效果 benchmark；效果验证
使用 `forge bench swebench ...`。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

Windows 原生验证入口默认只执行 package import 与已安装的 `forge --help`，用于快速确认开发
环境可用。设置当前进程的 `DEEPSEEK_API_KEY` 后追加 `-ModelSmoke`，可只跑一次真实 API
Single-Agent read-only smoke，不必先跑全量测试。追加 `-Full` 才执行 compile、
mypy、CLI、unit regression 与 MCP；因此 `-Full`
应配合 setup 的 `-WithDev` 使用。脚本只从当前进程读取 `DEEPSEEK_API_KEY`；不接受 key
参数，不调用 `setx`，也不会把 key 写入 profile、注册表、`.env` 或 artifact。

```bash
scripts/verify_mcp.sh
```

启动内置 stdio MCP server，发现 `forge.*` tool，调用 offline `web_search` tool，并
验证 `mcp_tools.json` 能将 server 注册到 `ToolRegistry`。除非手动设置
`AGENT_FORGE_MCP_ALLOW_NETWORK=1`，否则不会使用 network。

Windows 对应入口为：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_mcp.ps1
```

该脚本强制使用 offline provider，并在退出时恢复当前 PowerShell 进程的 PATH 与 provider
环境变量。
