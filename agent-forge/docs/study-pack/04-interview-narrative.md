# 04 面试讲法：如何描述这个项目

这份文档帮你把项目讲成一条完整故事线。面试官不是只想听功能列表，他想知道你是否理解问题、设计取舍、失败模式和证据。

## 30 秒版本

I built Agent Forge, a compact coding-agent harness, to show how an LLM can become a controlled execution system. It includes context assembly, tool calling, permission checks, sandboxed execution, observation feedback, tracing, and executable eval cases.

中文理解：

> 我做了一个 coding agent harness，重点不是模型本身，而是 LLM 外面的控制层：怎么给上下文、怎么调用工具、怎么做安全边界、怎么把工具结果反馈回 loop、怎么用 trace 和 eval 证明它真的跑通。

## 1 分钟版本

Agent Forge starts from a user task and first applies input guardrails. Then it builds context from repo map, memory, retrieval, symbol search, file ranking, and tool schemas. In single-agent mode, AgentLoop calls either MockLLM or an OpenAI-compatible LLM. If the model returns tool calls, the runtime checks tool guardrails and permission policy before executing through ToolRegistry inside a workspace sandbox. Tool results become Observations and go back into the next loop iteration. The run is recorded as trace JSON, and eval cases run real verify scripts to produce evidence.

中文理解：

> 用户任务进来后，先过 input guardrail，然后构建上下文。single mode 里 AgentLoop 调用 MockLLM 或 OpenAI-compatible LLM。如果模型返回 tool call，runtime 不会直接执行，而是先过 tool guardrail、permission，再通过 ToolRegistry 在 workspace sandbox 里执行。结果统一变成 Observation 回到下一轮。每次运行都有 trace，eval case 会真实执行 verify.py，所以项目不是只靠口头说能跑。

## 3 分钟版本结构

### 1. Problem

普通 chatbot 只生成文本，但 coding agent 必须能：

- 读仓库；
- 选择工具；
- 执行动作；
- 观察结果；
- 根据失败继续修正；
- 留下可审计证据。

### 2. Architecture

五层：

```text
CLI / Mode selection
Runtime loop
Tools and sandbox
Context engineering
Observability and eval
```

### 3. Hardest Part

最难的不是调模型 API，而是让工具执行可控：

- LLM 可能 hallucinate tool；
- 工具参数可能错；
- 命令可能危险；
- 文件访问可能越界；
- 模型可能没跑测试却声称测试通过；
- trace 必须能解释每一步。

### 4. Design Choices

| 选择 | 为什么 |
| --- | --- |
| 默认 MockLLM | demo/eval 稳定，可离线跑。 |
| OpenAI-compatible API | 可以切公司 API、MiniMax、ChatGPT、Ollama。 |
| ToolRegistry | 隔离模型输出和工具执行。 |
| WorkspaceSandbox | 限制文件边界。 |
| CommandPolicy | 限制危险命令和网络命令。 |
| Trace JSON | 每一步可审计。 |
| eval_cases | 用可执行验证证明能力。 |

### 5. Evidence

你可以现场跑：

```bash
scripts/run_all_modes.sh
scripts/verify.sh
```

证据：

- single demo 修复 demo repo；
- multi demo 展示 supervisor retry；
- workflow demo 展示确定性流程；
- unittest 全过；
- eval report 生成；
- trace JSON 可审计。

## 面试官问“你负责了什么”

推荐回答：

> 我负责把项目从一个能跑的 demo，整理成一个可学习、可解释、可验证的 agent harness。我重点处理了四块：第一是 CLI 和运行脚本，让它能在 macOS 和 WSL 复现；第二是 LLM 配置抽象，让 Mock、Ollama、公司 API 和 OpenAI-compatible provider 可以平滑切换；第三是核心 runtime 的可读性，让 AgentLoop、ToolRegistry、LLM client 的边界更清楚；第四是补 trace、eval 和学习文档，让项目能支撑面试深挖。

## 面试官问“为什么这个项目有价值”

推荐回答：

> 它把 coding agent 的控制层拆开了。很多人只会说调用 LLM，但真实 agent 工程难点在工具执行、安全边界、上下文选择、失败恢复和可观测性。这个项目虽然小，但覆盖了这些核心问题，而且每个能力都有本地可复现的运行证据。

## 面试官问“和 LangGraph / OpenCode / Claude Code 有什么区别”

推荐回答：

> LangGraph 是通用 workflow/agent graph 框架，OpenCode/Claude Code 是成熟产品。Agent Forge 不是要替代它们，而是学习型 harness，目标是把控制层显式写出来。这样我能讲清楚 agent loop、tool schema、permission、sandbox、trace、eval 这些底层概念，而不是只会配置框架。

## 面试官问“为什么不用真实 LLM 做所有测试”

推荐回答：

> 真实 LLM 有随机性、网络依赖和成本。核心 runtime 的 correctness 应该先用 deterministic MockLLM 验证。真实 LLM 路径保留为 OpenAI-compatible client，用于 integration demo，而不是让所有单测依赖外部服务。

## 面试官问“项目还有什么不足”

推荐回答：

> 这个项目是 harness MVP，不是生产 agent 平台。当前 multi-agent 是教学版 supervisor workflow，还不是 AgentLoop-backed 并发 scheduler；sandbox 是 workspace-level，不是容器级隔离；tool schema 还不是完整 JSON Schema；symbol search 是 AST MVP，不是完整 LSP；OpenAI-compatible client 没做流式、重试、限流、成本追踪。下一步我会加 runtime-backed subagents、任务 DAG 调度、model gateway、LSP provider、eval history 和更强的 tool schema validation。

## 面试官问“multi-agent 为什么这么线性”

推荐回答：

> 当前 multi mode 是故意做成线性的教学实现。它要展示的是 supervisor handoff、phase transition、tester failure retry、review gate 和 trace，而不是宣称已经实现生产级 multi-agent。生产级我会把 AgentLoop 抽成通用 AgentRuntime，每个 subagent 都有自己的 prompt、context、tool 权限和 stop condition；supervisor 负责构建任务 DAG、并发调度、处理 patch 冲突、聚合结果和决定是否升级人工审批。

## 结尾反问

如果面试官允许，你可以主动说：

> I can draw the architecture and walk through one trace if that helps.

然后画 `01-code-map-and-architecture.md` 里的图，再用 `trace-single.pretty.json` 讲一次完整 loop。
