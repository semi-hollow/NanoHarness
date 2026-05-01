# 20-resume-bullet-and-project-script

## 中文简历项目描述

Agent Forge：从零到 V2.1 构建一个标准库优先的 Agent Harness，覆盖 agent loop、tool calling、多 agent handoff、workflow mode、context engineering、permission/sandbox、guardrail、trace、metrics 和 eval benchmark。项目保留离线 MockLLM demo，同时新增可选 OpenAI-compatible LLM client、MCP-style tool adapter、symbol search/file ranking/context budget report，并用 19 个 eval case 和 unittest 验证安全与恢复路径。

## English Resume Bullet

Built Agent Forge, a standard-library-first Python agent harness with tool calling, safety sandboxing, observability, context retrieval, and evaluation; extended it to V2 with an optional OpenAI-compatible LLM client, MCP-style tool adapters, symbol search/file ranking, trace-derived metrics, and a 16-case executable eval benchmark.

## 1 分钟中文项目介绍

Agent Forge 是我做的一个 Agent Harness 项目，目标不是训练模型，而是把一个 Agent 在工程里真正需要的控制链路拆开：LLM 输出 tool call，agent loop 做权限判断，ToolRegistry 执行工具，Observation 回到循环，同时 trace 记录每一步。V1 能跑单 agent、多 agent 和 workflow demo；V2.1 增加了可选真实 LLM client、context budget report、symbol search、MCP-style adapter、metrics 和 19 个 eval case。这个项目最适合面试深挖，因为每个设计都有可运行代码、测试和文档证据。

## 3 分钟中文 Project Deep-Dive

我把项目分成五层。第一层是 runtime：`AgentLoop` 接收任务，调用 LLM，解析 tool calls，并把工具结果作为 Observation 放回消息流。第二层是 tool layer：内置 read/write/patch/run/git/ask_human，同时 V2 增加 MCP-style adapter，让 mock external tool 能转成统一 Tool schema 并注册执行。第三层是 safety：input/output guardrail、permission policy、workspace sandbox 和 command policy，覆盖敏感文件、危险命令、写入审批、虚假测试声明。第四层是 context：repo_map、RAG、memory、symbol_search、file_ranker 和 context budget report，说明为什么不是把全仓库塞进 prompt。第五层是 observability/eval：trace JSON 记录事件，metrics summary 统计 tool call、失败工具、handoff、guardrail block、approval、duration，eval runner 真实执行 19 个 case 的 verify.py 并生成报告。

这个项目的重点是工程边界。默认 MockLLM 保证离线可演示，可选 OpenAI-compatible client 通过环境变量接真实模型，并对 invalid response 返回结构化错误。MCP adapter 和 LSP 文档说明了未来扩展方向，但 V2 先实现可测试的本地 adapter 和 AST symbol search。面试时我会强调：我没有编造线上指标，项目证据来自 eval case 数、unittest、trace、metrics 和 safety case。

## 1 Minute English Intro

Agent Forge is a compact Python agent harness focused on the engineering control plane around LLM agents. It includes an agent loop, tool registry, observations, permission checks, sandboxing, guardrails, tracing, and executable evaluations. V2 keeps the offline MockLLM demo, then adds an optional OpenAI-compatible client, MCP-style local tool adapters, symbol search, file ranking, context budget reports, trace-derived metrics, and a 16-case eval benchmark. The project is designed for interviews because every claim maps to code, tests, docs, and a runnable command.

## 3 Minute English Deep-Dive

I usually explain Agent Forge in five layers. The runtime layer owns the agent loop: it sends messages to the LLM, receives tool calls, checks permissions, executes tools, and feeds observations back into the loop. The tool layer uses a simple ToolRegistry and now supports MCP-style local adapters, so external tools can enter the same schema and Observation contract. The safety layer includes input and output guardrails, command policy, workspace sandboxing, and human approval for writes. The context layer builds repo maps, retrieves docs, scans Python symbols with AST, ranks files, and reports the context budget. Finally, the observability and eval layer writes JSON traces, derives metrics, and runs executable eval cases.

The important trade-off is that V2 stays standard-library-first and deterministic by default. The OpenAI-compatible client is optional and returns structured invalid-response errors. The MCP adapter is not a full MCP protocol implementation, and symbol search is not a full LSP integration. Those choices make the project easy to run locally while still showing how I would extend it toward production through a model gateway, LSP provider, CI runner, or GitHub PR bot.

## STAR 版本

- Situation：Agent 项目常见问题是 demo 能跑，但无法解释安全、上下文、观测和评估。
- Task：我希望把一个 MVP 升级成面试可深挖、文档可学习、架构可扩展的项目。
- Action：我实现 agent loop、tool registry、sandbox、guardrail、trace、eval，并在 V2 增加 OpenAI-compatible client、MCP-style adapter、symbol search、file ranker、context budget report 和 metrics。
- Result：项目可以通过 single/multi/workflow demo、unittest、py_compile 和 19 个 eval case；面试材料能从简历 bullet 一直讲到生产化 rollout。

## 4 层追问清单

| 层级 | 追问方向 | 回答证据 |
| --- | --- | --- |
| L1 | 这个项目是什么，为什么不是普通 demo？ | README、agent loop、eval report |
| L2 | tool call 失败、权限拒绝、输出幻觉怎么处理？ | ToolRegistry、guardrails、sandbox、eval safety cases |
| L3 | 为什么 context 不直接塞全仓库？ | context budget report、file_ranker、symbol_search |
| L4 | 如果上线成 PR bot 怎么做？ | production readiness、model gateway、audit、rollback、rollout |

## 白板架构图讲法

先画左侧 User task，进入 input guardrail。中间画 AgentLoop，它向上调用 LLM client，向右调用 ToolRegistry。ToolRegistry 下挂 built-in tools 和 MCP-style adapter，外面包 permission policy 和 sandbox。工具返回 Observation 回到 AgentLoop。右侧画 Trace JSON，再派生 Metrics 和 Eval Report。最后补一条 context 旁路：repo_map、memory、retrieval、symbol_search、file_ranker 共同组成 prompt context。讲的时候强调所有箭头都有测试或 eval case，不讲线上 QPS、节省成本这类没有证据的指标。
