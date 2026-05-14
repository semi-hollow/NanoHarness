# Agent Forge 从 0 到 1 掌握指南

这份文档的目标不是只告诉你“命令怎么跑”，而是帮你把 Agent Forge 跑成一个可以面试深挖的项目：你知道入口在哪里、每种模式证明什么、trace 怎么读、LLM 怎么替换、面试官追问时怎么回答。

## 1. 项目定位

Agent Forge 是一个 coding agent harness。它不是模型，也不是 Claude Code / Codex 的复制品。它展示的是 coding agent 的控制层：

- Agent loop：模型输出 action，工具执行，observation 回到下一轮。
- Tool calling：工具 schema、参数校验、统一 Observation。
- Safety：input/output guardrail、permission、sandbox、command policy。
- Context：repo map、memory、RAG、symbol search、file ranking、token budget。
- Multi-agent：Supervisor 编排 Planner / Coding / Tester / Reviewer。
- Observability：trace JSON、metrics、eval report。
- Evaluation：19 个 eval case，每个 case 真实执行 `verify.py`。

面试时一句话讲法：

> I built a compact coding-agent harness to make the control layer explicit: context assembly, tool routing, permission checks, sandboxed execution, observation feedback, tracing, and executable evaluation.

## 先读 Study Pack

如果你现在的目标是“看着代码就能理解项目”，先读 `docs/study-pack/`：

1. `docs/study-pack/01-code-map-and-architecture.md`
2. `docs/study-pack/02-key-file-walkthrough.md`
3. `docs/study-pack/03-run-modes-and-trace-reading.md`
4. `docs/study-pack/04-interview-narrative.md`
5. `docs/study-pack/05-deep-dive-prep.md`
6. `docs/study-pack/06-personal-study-checklist.md`

这组文档按“代码地图 -> 关键文件 -> 运行 trace -> 面试表达 -> 深挖追问 -> 自测清单”组织，目的是让你不用每次把 GitHub 链接发给外部 GPT。

## 2. 首次初始化

### macOS

```bash
cd /Users/chenjiahui/Documents/GitHub/NanoHarness/agent-forge
scripts/setup_macos_local.sh
```

日志：

```bash
~/agent_forge_macos_setup.log
```

### Windows WSL Ubuntu

建议把项目放在 WSL 内部路径，比如：

```bash
~/repo/ai/NanoHarness/agent-forge
```

然后运行：

```bash
cd ~/repo/ai/NanoHarness/agent-forge
scripts/setup_wsl_local.sh
```

日志：

```bash
~/agent_forge_wsl_setup.log
```

如果缺系统依赖，先安装：

```bash
sudo apt update
sudo apt install -y git curl build-essential python3 python3-venv python3-pip rsync jq
```

## 3. 日常运行顺序

每次开始学习或演示前：

```bash
cd /path/to/NanoHarness/agent-forge
source .venv/bin/activate
```

先跑全模式：

```bash
scripts/run_all_modes.sh
```

再跑完整验证：

```bash
scripts/verify.sh
```

看到这些就说明正确：

- `Final: pass`
- `final_status='success'`
- `Ran 45 tests ... OK` 或更高测试数
- `eval_report.md generated`
- `compileall` 没有 `SyntaxError`

`tool_observation success=False` 不一定代表失败。这个项目会故意让一次 patch 失败，然后通过下一步 recovery 修复，这正是 agent loop 的学习重点。

## 4. 三种运行模式怎么理解

### single

```bash
python run_demo.py --mode single --trace-file trace-single.json
```

你要观察：

- MockLLM 先读 `calculator.py`。
- 再读测试。
- 第一次 patch 故意失败。
- 第二次 patch 修复。
- 最后跑 unittest。

面试重点：Agent loop 的 action-observation 闭环，以及失败恢复。

### multi

```bash
python run_demo.py --mode multi --trace-file trace-multi.json
```

你要观察：

- SupervisorAgent 统一编排。
- PlannerAgent 先规划。
- CodingAgent 修代码。
- TesterAgent 发现失败后触发 retry。
- ReviewerAgent 最后 review。

面试重点：Subagent 不是互相乱调，而是 supervisor-driven handoff。

### workflow

```bash
python run_demo.py --mode workflow
```

你要观察：

- 这是确定性流程，不依赖 LLM。
- 输出 `WorkflowState`。

面试重点：workflow 和 agent 的差异。Workflow 适合稳定流程；agent 适合需要根据 observation 动态决策的任务。

## 5. LLM 如何平滑切换

默认是 MockLLM：

```bash
python run_demo.py --mode single
```

### 方式 A：命令行直接传

```bash
python run_demo.py --mode single --llm openai \
  --base-url http://localhost:11434/v1 \
  --api-key ollama \
  --model qwen2.5-coder:7b \
  --trace-file trace-ollama.json
```

### 方式 B：环境变量

```bash
export AGENT_FORGE_BASE_URL="https://your-api-host/v1"
export AGENT_FORGE_API_KEY="your-api-key"
export AGENT_FORGE_MODEL="your-model"

python run_demo.py --mode single --llm openai --trace-file trace-online.json
```

兼容别名：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

### 方式 C：本地 profile

复制示例：

```bash
cp llm_profiles.example.json llm_profiles.json
```

编辑 `llm_profiles.json`，然后运行：

```bash
python run_demo.py --mode single --llm-profile ollama-qwen
```

或：

```bash
local_scripts/run_llm_profile.sh ollama-qwen
```

不要提交真实 key。`llm_profiles.json` 已被 `.gitignore` 忽略。

## 6. Ollama

先确认 Ollama 可用：

```bash
ollama list
ollama pull qwen2.5-coder:7b
ollama serve
```

运行：

```bash
local_scripts/run_ollama.sh
```

如果连接不上，试：

```bash
AGENT_FORGE_BASE_URL=http://127.0.0.1:11434/v1 local_scripts/run_ollama.sh
```

## 7. Trace 怎么看

格式化：

```bash
python -m json.tool trace-single.json > trace-single.pretty.json
```

重点看：

- `run_context.task`：这次任务是什么。
- `events[].event`：guardrail、context、plan、llm_call、tool_call、observation。
- `events[].success`：工具或检查是否成功。
- `metrics`：tool call、失败工具、handoff、approval、duration。
- `final_answer`：最终回答。

读 trace 的顺序：

1. 先找 `llm_call`，看模型想做什么。
2. 再找 `tool_call`，看 runtime 实际执行什么。
3. 再找 `tool_observation`，看执行结果如何反馈给下一轮。
4. 最后看 `final_answer` 和 `metrics`。

## 8. 代码阅读路径

按这个顺序读，效率最高：

1. `run_demo.py`：项目入口。
2. `agent_forge/cli.py`：CLI 参数、mode 选择、LLM 选择。
3. `agent_forge/runtime/agent_loop.py`：核心 action-observation loop。
4. `agent_forge/tools/registry.py`：工具注册、schema、执行入口。
5. `agent_forge/tools/*.py`：具体工具。
6. `agent_forge/safety/*.py`：权限、sandbox、guardrail。
7. `agent_forge/context/*.py`：上下文构建。
8. `agent_forge/agents/supervisor_agent.py`：多 agent 编排。
9. `agent_forge/eval/eval_runner.py`：eval 如何真实执行。
10. `agent_forge/observability/trace.py`：trace 如何生成。

## 9. 面试怎么讲

### 30 秒版本

我做的是一个 compact coding-agent harness，用来展示 LLM 如何从文本生成器变成受控执行系统。核心是 agent loop、tool calling、permission/sandbox、context assembly、trace 和 eval。

### 1 分钟版本

这个项目里，用户任务先经过 input guardrail，然后构建上下文，包括 repo map、memory、retrieval 和 symbol search。AgentLoop 调用 MockLLM 或 OpenAI-compatible LLM，如果返回 tool call，就先做 tool guardrail 和 permission check，再通过 ToolRegistry 执行工具。工具执行被 workspace sandbox 和 command policy 限制，结果统一变成 Observation 回到下一轮。每次运行都会写 trace JSON，eval runner 会真实执行 19 个 case 的 verify.py，所以我能用本地可复现证据说明功能是否真的成立。

### 高频追问

Q: 为什么默认用 MockLLM？

A: 为了让 demo 和 eval 稳定可复现。真实模型有随机性和服务依赖，MockLLM 让控制层逻辑可以离线测试。项目同时支持 OpenAI-compatible API，所以可以切到公司 API、MiniMax、ChatGPT 或 Ollama。

Q: Workflow 和 Agent 的区别？

A: Workflow 是固定路径，适合稳定流程；Agent 是根据 observation 动态决定下一步。项目保留两种模式，是为了在面试里清楚讲 trade-off。

Q: 安全边界在哪里？

A: 不只靠 prompt。工具执行前有 permission policy 和 command policy，路径访问经过 workspace sandbox，输出还有 guardrail 防止未测试却声称测试通过。

Q: eval 为什么可信？

A: 每个 eval case 都有独立 `verify.py`，runner 用当前 Python 真实执行，而不是硬编码全通过。

## 10. 你应该亲手完成的练习

1. 跑 `scripts/run_all_modes.sh`，对比 single/multi/workflow 输出。
2. 打开 `trace-single.pretty.json`，标出每一次 tool call 和 observation。
3. 把 `MockLLMClient` 的第一次 patch 改成直接成功，再观察 trace 变化。
4. 用 `--no-auto-approve` 跑 single，观察 human approval 被拒绝时的行为。
5. 复制 `llm_profiles.example.json`，配置一个 Ollama profile。
6. 用公司 API 或自己的 API 跑 `--llm-profile`。
7. 阅读 `docs/17-architecture-whiteboard.md`，用白板讲一遍架构。
8. 阅读 `docs/14-interview-qa.md`，挑 10 个问题口头回答。

完成这些，你就不是“跑过项目”，而是能解释它为什么这样设计、失败模式是什么、下一步怎么演进。
