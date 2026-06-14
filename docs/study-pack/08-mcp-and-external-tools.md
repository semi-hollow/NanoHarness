# 08 MCP And External Tools

这一篇只解释 `schema` 分支新增的 MCP / 外部工具能力。读完以后，你应该能回答三个问题：

1. 这个项目为什么需要 MCP？
2. Agent 是怎么启动 MCP server 并调用工具的？
3. 为什么 web search / web fetch 不直接写进 `AgentLoop`？

## 一句话定位

MCP 在这个项目里是外部工具协议边界，不是又一个业务功能。它让 Agent 可以把工具放到独立进程里，通过统一的 discovery、schema、allowlist、tool call、observation 接入主循环。

本项目现在同时具备两侧能力：

- Client side：`agent_forge/tools/mcp_config.py` 和 `agent_forge/tools/mcp_stdio.py`。
- Server side：`agent_forge/mcp/` 里的内置 stdio MCP server。

## 新增内容总览

| file | 作用 | 先看什么 |
|---|---|---|
| `agent_forge/mcp/server.py` | 最小 MCP-style JSON-RPC server，支持 `initialize`、`tools/list`、`tools/call`。 | `AgentForgeMCPServer.handle_request()` |
| `agent_forge/mcp/web_tools.py` | 内置工具实现：repo policy、time、web search、web fetch。 | `build_builtin_tools()`、`_web_search()` |
| `agent_forge/mcp/builtin_server.py` | 命令行入口，可直接 list/call，也可作为 stdio server 被 agent 启动。 | `main()` |
| `agent_forge/tools/mcp_stdio.py` | MCP client，负责启动 server 进程、发 JSON-RPC、收响应。 | `discover_tools()`、`call_tool()` |
| `agent_forge/tools/mcp_config.py` | 读取 `mcp_tools.example.json`，把远端工具注册进 `ToolRegistry`。 | `_register_stdio_server()` |
| `mcp_tools.example.json` | 默认 MCP 配置，启动内置 server，并 allowlist `forge.*` 工具。 | `allowed_tools`、`servers[0]` |
| `scripts/verify_mcp.sh` | 离线验证 MCP server、discovery、tool call、config registration。 | 整个脚本从上到下读 |
| `tests/test_mcp_builtin_server.py` | MCP server 的回归测试。 | 三个 test 名称 |

## 调用链

```mermaid
sequenceDiagram
    participant User as "User / CLI"
    participant CLI as "run_demo.py / cli.py"
    participant Loader as "MCPConfigLoader"
    participant Client as "MCPStdioClient"
    participant Server as "builtin MCP server"
    participant Registry as "ToolRegistry"
    participant Loop as "AgentLoop"
    participant LLM as "LLM"

    User->>CLI: --mcp-config mcp_tools.example.json
    CLI->>Loader: load_into(registry, config)
    Loader->>Client: create stdio client
    Client->>Server: start process
    Client->>Server: initialize
    Client->>Server: tools/list
    Server-->>Client: tool schemas
    Client-->>Loader: discovered tools
    Loader->>Registry: register MCPStdioTool
    Loop->>LLM: context + routed tool schemas
    LLM-->>Loop: tool_call forge.repo_policy / forge.web_search
    Loop->>Registry: execute(tool_name, args)
    Registry->>Client: tools/call
    Client->>Server: JSON-RPC tool call
    Server-->>Client: MCP content blocks
    Client-->>Registry: result
    Registry-->>Loop: Observation
```

这条链路的关键点是：`AgentLoop` 不知道工具来自本地 Python 类、配置文件、还是外部 MCP server。它只面对统一的 `Tool` 接口和统一的 `Observation`。

## 为什么需要 MCP

如果所有工具都写成本地类，项目短期更简单，但有几个工程问题：

- 工具生命周期和 agent 主进程绑死，某个工具崩溃可能影响主循环。
- 外部系统接入方式不统一，每接一个工具都要改 runtime。
- 工具 schema、权限、trace、观测容易散落在不同位置。
- 以后接公司内部服务、搜索服务、浏览器服务、数据查询服务时，很难标准化治理。

MCP 的价值是把工具变成协议资源：

- `tools/list` 负责发现能力。
- `inputSchema` 负责让模型知道参数格式。
- `tools/call` 负责执行。
- `isError` 和 content blocks 负责把结果变成可恢复的 observation。

## 内置工具说明

| tool | 作用 | 为什么存在 |
|---|---|---|
| `forge.repo_policy` | 读取或搜索 `FORGE.md`。 | 让 agent 在改代码前通过工具拿到项目规则，而不是只依赖 prompt 里的一段静态文本。 |
| `forge.current_time` | 返回 local time 和 UTC time。 | 给时间敏感任务一个确定性工具，避免模型凭空猜时间。 |
| `forge.web_search` | 查询外部信息。默认 offline，可切到 DuckDuckGo、OpenAI、Claude。 | 证明 agent 可以接外部信息源，同时保留网络开关和 provider 抽象。 |
| `forge.web_fetch` | 拉取单个 HTTP/HTTPS 页面并转成文本。 | search 只解决发现，fetch 解决读取具体页面；两者拆开更可控。 |

## 运行方式

先激活虚拟环境：

```bash
cd /path/to/NanoHarness
source .venv/bin/activate
```

验证 MCP 全链路：

```bash
scripts/verify_mcp.sh
```

直接看 server 暴露了哪些工具：

```bash
python -m agent_forge.mcp.builtin_server --workspace . --list-tools
```

直接调用一个工具：

```bash
python -m agent_forge.mcp.builtin_server --workspace . \
  --call web_search \
  --args-json '{"query":"agent tool protocol","max_results":1}'
```

让 agent 加载 MCP 工具：

```bash
python run_demo.py \
  --mcp-config mcp_tools.example.json \
  --mcp-allowed-tool forge.repo_policy \
  "use the repo_policy tool to summarize command policy"
```

主 WebhookPatchBench 脚本已经默认加载 MCP：

```bash
local_scripts/run_webhook_deepseek.sh
```

## 联网查询怎么开

默认 `forge.web_search` 是 offline 模式。这样公司电脑或离线环境也能验证 MCP 协议，不会偷偷访问外网。

启用 DuckDuckGo：

```bash
AGENT_FORGE_MCP_ALLOW_NETWORK=1 \
AGENT_FORGE_WEB_PROVIDER=duckduckgo \
python run_demo.py --mcp-config mcp_tools.example.json \
  "search public information about MCP tool protocol"
```

启用 OpenAI hosted web search：

```bash
AGENT_FORGE_MCP_ALLOW_NETWORK=1 \
AGENT_FORGE_WEB_PROVIDER=openai \
OPENAI_API_KEY="your-api-key" \
python run_demo.py --mcp-config mcp_tools.example.json \
  "search current public information about coding agents"
```

启用 Claude hosted web search：

```bash
AGENT_FORGE_MCP_ALLOW_NETWORK=1 \
AGENT_FORGE_WEB_PROVIDER=claude \
ANTHROPIC_API_KEY="your-api-key" \
python run_demo.py --mcp-config mcp_tools.example.json \
  "search current public information about coding agents"
```

注意：不要把真实 key 写进仓库。真实 key 只放在本机 shell 环境或被 `.gitignore` 忽略的本地文件里。

## 设计取舍

### 为什么默认 offline

默认离线是为了让 MCP 成为稳定的工程能力，而不是依赖外网状态的演示。它保证：

- 公司机器能跑。
- 单测和 `scripts/verify_mcp.sh` 不消耗 token。
- 没有 key 时也能验证 discovery 和 tool call。
- 真要联网时必须显式打开，便于审计。

### 为什么 web search 放在 MCP 后面

Web search 本质是外部能力，不应该污染 `AgentLoop`。如果直接在 `AgentLoop` 里写 provider 判断，会让主循环变成 provider 拼接层。

现在的边界是：

- `AgentLoop`：只负责 context、LLM、tool call、observation、recovery。
- `ToolRegistry`：只负责工具查找、参数校验、异常转 observation。
- `MCPStdioClient`：只负责协议通信。
- `agent_forge/mcp/web_tools.py`：只负责外部 provider 的具体 HTTP 调用。

这样更容易替换 provider，也更容易解释权限、trace、失败恢复。

### 为什么加 `cwd`

MCP server 是独立进程。如果不显式设置工作目录，server 从哪里读取 `FORGE.md` 会依赖用户启动命令的位置。

`MCPStdioServerSpec.cwd` 和 `mcp_tools.example.json` 的 `cwd` 让 server 的 workspace 稳定下来。这是生产系统常见的坑：外部工具进程必须有明确的 working directory、env、timeout。

### 为什么 `python` 会解析成当前解释器

不同机器可能只有 `python3`，没有 `python`；也可能当前项目必须使用 `.venv/bin/python`。`MCPConfigLoader._resolve_command()` 会把 `python` / `python3` 解析成当前运行 `run_demo.py` 的解释器，保证 MCP server 和主进程使用同一个虚拟环境。

## 常见追问

### MCP 和 Function Calling 有什么区别

Function calling 是模型输出结构化 tool call 的方式。MCP 是工具服务发现和调用协议。前者偏模型接口，后者偏工具生态和运行边界。

这个项目里，模型仍然通过 tool schema 选择工具；MCP 负责把工具从独立 server 暴露出来，再转成 `ToolRegistry` 里的普通工具。

### 如果工具失败怎么办

MCP tool handler 的失败会变成 `isError=True` 的工具结果，再由 `MCPStdioTool.execute()` 转成 failed `Observation`。这意味着失败会回到 AgentLoop，而不是直接把主进程打崩。

### 如果 provider 超时怎么办

`web_tools.py` 里有 `AGENT_FORGE_MCP_TIMEOUT`，默认短超时，并且每次 stdio request 都有 `MCPStdioServerSpec.timeout_seconds`。这两层分别约束外部 HTTP 和 server 通信。

### 现在还缺什么

当前实现已经能证明 MCP server 启动、工具发现、工具调用、agent 注册和 observation 回传。还没有做的是：

- 长驻 MCP server 池化。
- OAuth / marketplace / remote HTTP MCP。
- 浏览器自动化工具。
- 企业内部工具网关。
- 工具级配额和限流。

这些是扩展方向，不应该混进当前主线，否则会增加启动成本和阅读成本。

## 阅读建议

按这个顺序看代码：

1. `mcp_tools.example.json`
2. `agent_forge/tools/mcp_config.py`
3. `agent_forge/tools/mcp_stdio.py`
4. `agent_forge/mcp/server.py`
5. `agent_forge/mcp/web_tools.py`
6. `scripts/verify_mcp.sh`
7. `tests/test_mcp_builtin_server.py`

然后跑：

```bash
scripts/verify_mcp.sh
local_scripts/run_webhook_deepseek.sh
```

看 trace 时重点搜：

- `mcp_tools_loaded`
- `forge.repo_policy`
- `forge.web_search`
- `tool_call`
- `tool_observation`

