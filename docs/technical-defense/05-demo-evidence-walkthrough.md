# 05 演示和证据讲法

这份文件用于现场演示或自我复盘。目标是：你说的每个能力，都能指到一个运行命令、
一个 trace 事件、一个 usage 数字或一个代码文件。

## 演示前准备

```bash
cd /Users/chenjiahui/Documents/GitHub/NanoHarness
source .venv/bin/activate
```

如果要用真实模型：

```bash
echo "$DEEPSEEK_API_KEY"
```

不要在终端里打印完整 key 给别人看；这里只是确认环境变量存在。

## 主演示路径

### 1. 跑 WebhookPatchBench

```bash
local_scripts/run_webhook_deepseek.sh
```

讲法：

```text
这是我现在主推的真实代码修改场景。它不是 calculator smoke test，而是一个小型 webhook service。
任务要求 agent 修复 duplicate event_id 导致重复入库/重复 enqueue 的问题，同时不能绕过签名校验，不能读取 secret，要跑 unittest。
```

生成重点：

```text
.agent_forge/latest/webhook-deepseek/usage_report.md
.agent_forge/latest/webhook-deepseek/trace.json
.agent_forge/runs/<run_id>/report.md
```

### 2. 先打开 usage report

```bash
open .agent_forge/latest/webhook-deepseek/usage_report.md
```

重点讲这些段落：

| 段落 | 怎么讲 |
|---|---|
| Run Summary | 总 LLM calls、token、cache、cost、latency、tool calls。 |
| Runtime Control | 当前执行环境、approval/hook/task status。 |
| Step Breakdown | 每轮模型调用的 token、cost、context chars、action summary。 |
| Context Breakdown | prompt 预算花在哪里，是否 context 膨胀。 |
| Tool Efficiency | 哪些工具被调用，成功率和失败 observation。 |

推荐口径：

```text
我不会只说 agent 跑通了。我会看每步花了多少 token、上下文预算花在哪里、哪些工具失败了、失败是否被恢复。
这也是 coding agent 工程化和普通 demo 的区别。
```

### 3. 再打开 trace

```bash
code .agent_forge/latest/webhook-deepseek/trace.json
```

或者用命令行回放：

```bash
python run_demo.py --replay-run .agent_forge/latest/webhook-deepseek/trace.json
```

重点搜索：

```text
context_assembly
llm_call
action
hook_check
permission_check
tool_observation
recovery_decision
final_answer
```

讲法：

```text
trace.json 是事实源。它能解释模型每一步为什么看到这些上下文、为什么调用这个工具、工具是否成功、permission 怎么判定、最后回答有没有证据。
```

### 4. 展示 review gate

```bash
python run_demo.py --mode review
```

讲法：

```text
我不直接信 agent 的 final answer。代码修改后还需要 deterministic review gate 看 git diff，识别签名绕过、secret、shell 风险、缺少测试等问题。
```

### 5. 展示 MCP

```bash
scripts/verify_mcp.sh
python -m agent_forge.mcp.builtin_server --workspace . --list-tools
```

讲法：

```text
MCP 在这里不是概念文档，而是真的能启动 server、tools/list discovery、tools/call 调用，再注册进 ToolRegistry。
AgentLoop 不关心工具来自本地类还是外部 server。
```

## 如果 DeepSeek 运行失败怎么讲

常见失败：

| 现象 | 解释 |
|---|---|
| 400 Bad Request | provider payload、model、base URL 或 tool schema 不兼容。 |
| max_steps reached | agent 没在预算内收敛，需要看 trace 里的 repeated action、failed observation、context selection。 |
| command blocked | runtime policy 生效，不是 bug。 |
| missing API key | 个人机器需要 `DEEPSEEK_API_KEY`，公司机器用 mock。 |

不要慌，失败本身也是 Agent 工程经验。重点是你能用 trace 定位。

## 演示时的三条证据链

### 能力证据链

```text
Webhook issue -> context_assembly selected files -> apply_patch -> unittest -> final answer
```

### 安全证据链

```text
tool call -> hook_check -> permission_check -> sandbox/command policy -> observation
```

### 成本证据链

```text
llm_call -> provider usage -> cache hit/miss -> estimated cost -> usage report summary
```

## 最后收尾怎么说

```text
这个项目不是为了证明我封装了一个 LLM API，而是为了证明我理解 CodingAgent 生产化时真正难的 runtime 问题：
上下文怎么选，工具怎么管，失败怎么恢复，权限怎么控，运行怎么审计，成本怎么量化，badcase 怎么回归。
```
