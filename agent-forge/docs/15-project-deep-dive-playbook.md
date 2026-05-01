# 15-project-deep-dive-playbook

## 30 秒版本

我做的是 Agent Forge，一个 compact Agent Harness。它不是聊天机器人，也不是复制 Claude Code，而是用一个可运行的 coding demo，把 Agent 工程里最核心的控制层拆出来：context assembly、tool calling、permission/sandbox、observation、trace 和 eval。当前真实证据是 single/multi/workflow demo 可跑，44 个 unittest 通过，19 个 eval case 全部通过。

## 1 分钟版本

我做 Agent Forge 是为了回答一个工程问题：怎么把 LLM 从文本生成器变成一个受控执行系统。项目默认用 MockLLM 保证无 API key 也能跑；AgentLoop 每轮会装配上下文、记录 plan summary、选择工具、执行工具、把 Observation 回传，再继续迭代。工具层通过 ToolRegistry 统一 schema、参数校验和错误处理；安全层通过 permission、sandbox、guardrails 拦截危险路径和命令；观测层用 JSON trace 和 metrics 记录 tool call、handoff、approval、失败工具和步骤数。最后用 19 个 eval case 证明不是只跑 happy path。

## 3 分钟版本

### 第 1 分钟：context + role

我做的是一个面向 Agent 工程学习和面试讲解的 harness。普通 chatbot 只能回答文本，但 coding agent 要真正改代码，需要上下文、工具、权限、反馈和评估闭环。我负责把这些机制用 Python 标准库拆成可运行模块：CLI、runtime、tools、context、safety、observability、eval、docs。

### 第 2 分钟：hardest problem + approach + trade-off

最难的不是调模型 API，而是 autonomy 和 control 的权衡。Agent 越自动，越容易出现工具幻觉、越权、死循环、错误 patch 和虚假成功。我把系统分层：MockLLM 让链路稳定可测；ToolRegistry 处理 unknown tool 和 invalid arguments；WorkspaceSandbox 和 command policy 控制执行边界；output guardrail 防止没跑测试却声称通过；trace 记录 context、plan、action、observation。trade-off 是第一版不用复杂框架、向量库或完整 LSP，而是先做可解释、可测试、可演进的最小实现。

### 第 3 分钟：result + evidence + learning

结果上，single demo 能修复 calculator bug 并跑测试，multi demo 有 Supervisor 到 Planner/Coder/Tester/Reviewer 的 handoff，workflow demo 展示固定流程，eval report 显示 19/19 通过，unittest 是 44 个测试通过。安全 case 覆盖 dangerous command、external path、secret file、unknown tool、invalid args、repeated tool call、human approval rejected、false test claim。我的反思是：这个项目不是生产 IDE agent，但它能把生产化前必须讨论的边界讲清楚。下一步是接真实 OpenAI-compatible model、LSP provider、完整 MCP protocol roadmap、CI result verification 和 eval history。

## English Conversational Version

I built Agent Forge as a small but complete agent harness. The point was not to wrap an LLM API, but to make the control layer explicit: context assembly, planning summary, tool routing, permission checks, sandboxing, observation feedback, tracing, and evaluation. I kept MockLLM as the default so the demo and tests are deterministic, and added an optional OpenAI-compatible client for real model integration. The strongest part of the project is that every claim has evidence: runnable demos, unit tests, trace events, and executable eval cases.

## 如果面试官中断

可以这样收束：

> 我先停在这里。核心点是：我把 Agent 的执行闭环、安全边界、观测和评估都做成了可运行证据。你想先深挖 loop、tool safety、multi-agent，还是 eval？

## 从 Feature List 改成 Problem-driven

弱表达：我做了 agent loop、tools、trace、eval。

强表达：我先识别 coding agent 的四个风险：模型会乱调用工具、工具会越权、执行过程不可追踪、结果可能无法验证。然后我分别用 ToolRegistry、Permission/Sandbox、Trace、Eval 去控制这些风险。

## 真实指标

- unittest：44 tests passed。
- eval：19 total / 19 passed / 0 failed / 100.0% pass rate。
- single demo trace：包含 context、plan、action、permission、approval、tool observation、final answer。
- multi demo：包含 Planner/Coding/Tester/Reviewer handoff。

这些是项目运行指标，不是业务线上指标。
