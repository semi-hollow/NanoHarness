# 03 项目包装和简历写法

这份文件用于把 Agent Forge 写成一个可信的技术项目。重点是：讲出 Senior 工程能力，
但不夸大成已经线上大规模商用。

## 中文一句话

Agent Forge 是一个 production-style CodingAgent runtime core，聚焦把 LLM 接入受控代码执行系统所需的上下文工程、工具治理、执行控制、MCP 外部工具、trace、usage、eval 和 review gate。

## 推荐定位

可以说：

- production-style CodingAgent runtime core
- CodingAgent harness
- 面向开源学习和工程验证的 runtime 参考实现
- 聚焦 runtime control plane，而不是产品 UI 或云平台

不要说：

- 已经是 Codex / Claude Code 同等级产品
- 已经线上服务真实用户
- 已经具备企业级容器调度、IDE 插件、远程 MCP marketplace
- 已经做了模型训练、SFT 或 RL

更稳的中文表达：

> 我实现的是一个 CodingAgent runtime core，不是完整 IDE 产品。它覆盖了这类系统最核心的控制面：上下文构建、模型网关、工具路由、沙箱和命令策略、审批 hooks、任务状态、MCP 工具接入、可观测、usage 报告、回归 eval，以及一个真实感更强的 webhook 代码修复场景。

## 简历项目标题

```text
Agent Forge：面向代码修改任务的 CodingAgent Runtime Core
```

或者更工程化一点：

```text
Agent Forge：具备上下文工程、工具治理和运行时控制的 CodingAgent Harness
```

## 简历项目描述

版本 A，偏架构：

```text
设计并实现 Agent Forge，一个 production-style CodingAgent runtime core，围绕代码修改任务构建 ReAct-style agent loop、上下文工程、模型网关、工具治理、执行环境、权限审批、任务状态、trace/usage 可观测和本地 eval 回归体系。
```

版本 B，偏业务场景：

```text
以 WebhookPatchBench 作为主验证场景，驱动 agent 在一个小型 webhook service 中完成 issue 理解、上下文选择、代码 patch、unittest 验证、安全策略保持、secret 访问阻断、review gate 和 per-step token/cost 复盘。
```

## 简历 bullet

- 设计 ReAct-style CodingAgent 主循环，支持 context assembly、LLM tool call、Observation feedback、失败恢复、最终回答 guardrail，并通过 trace 记录每一步决策证据。
- 构建上下文工程模块，实现 repo map、文件排序、lexical retrieval、selected file preview、memory summary、topic-shift 判断和 context budget breakdown，避免全仓库直接塞入 prompt。
- 实现工具治理层：ToolRouter 裁剪候选工具，ToolRegistry 做 schema validation 和异常转 Observation，HookManager 统一处理审批、执行环境检查和输出脱敏。
- 实现运行时控制面：StepController 支持 max steps、timeout、重复工具调用检测、失败分类和 cost budget；ExecutionEnvironment 支持 local/worktree、网络策略和 git 风险命令拦截。
- 接入 OpenAI-compatible 模型网关，支持 MockLLM、DeepSeek、Ollama/公司模型 API，并记录 provider usage、cache hit/miss、latency 和估算成本。
- 实现 stdio MCP 工具协议能力，支持启动内置 MCP server、发现 `forge.*` 工具、注册到 ToolRegistry，并将 web search/web fetch 放在外部工具边界后面。
- 建立 eval 和 review 机制，覆盖上下文检索、工具失败恢复、权限拦截、重复调用、OpenAI-compatible provider 异常、WebhookPatchBench 和 deterministic review gate。

## 项目亮点怎么讲

### 亮点 1：上下文工程不是 prompt 拼接

可以这样讲：

```text
我把上下文构建从 AgentLoop 里抽出来，做成 ContextBuildReport 和 ContextStrategy。
这样 trace 里能看到 selected_files、retrieved_docs、memory_summary、budget_breakdown 和 dropped_context。
如果 agent 漏了文件，我能判断是 retrieval 召回问题、budget 截断问题，还是 topic-shift 继承问题。
```

### 亮点 2：工具调用是治理系统

可以这样讲：

```text
我没有把 tool calling 当成简单 function call，而是拆成 ToolRouter、HookManager、ToolRegistry 和 Concrete Tool。
模型只提出工具名和参数；runtime 负责候选工具裁剪、权限审批、路径/命令边界、schema 校验、执行、脱敏、Observation 回传和失败分类。
```

### 亮点 3：可观测让运行结果可解释

可以这样讲：

```text
每次运行都会输出 trace.json 和 usage_report.md。trace 是事实源，记录 context/model/tool/hook/recovery；usage_report 是工程视角，汇总 per-step token、cache hit/miss、estimated cost、latency、context breakdown、tool efficiency。
```

## 容易被质疑的点和回答

| 质疑 | 回答 |
|---|---|
| 这是不是 toy project？ | 不是单次 prompt demo。项目实现了上下文、工具治理、权限、执行环境、失败恢复、trace、usage、eval、MCP 和 review gate。 |
| 为什么没有 IDE/TUI？ | 我刻意聚焦 runtime core。IDE/TUI 是产品层，可以接在 CLI/API 外面，不应该淹没核心控制面。 |
| 为什么没有线上用户？ | 这是开源 runtime 项目，不声称线上 SaaS。项目用 committed run artifacts、eval cases 和 WebhookPatchBench 展示可复现能力。 |
| Sandbox 够不够生产？ | 当前是 workspace sandbox + command policy + worktree isolation。生产会把 ExecutionEnvironment backend 换成 Docker/remote sandbox。 |
| Eval 是不是太小？ | 当前 eval 是 failure-mode regression，不是 leaderboard。下一步会做 automated benchmark matrix 和 trend history。 |
| Multi-agent 是不是线性？ | 当前主场景依赖关系天然线性；TaskGraph 支持 dependency/conflict-aware scheduling。生产并发属于 scheduler 层。 |

## 项目边界

这个项目不做：

- IDE/TUI 产品交互
- 云端容器调度和账号体系
- 大规模企业知识库 / GraphRAG 平台
- 多模态生成流水线
- 模型训练、SFT、RL
- 真实线上 SLA 和用户反馈系统

这些不是“不知道”，而是分层边界。当前仓库实现 runtime core；其他能力可以在 runtime 边界外扩展。

## GitHub 简介

中文短描述：

```text
面向代码修改任务的 CodingAgent runtime core，覆盖上下文工程、工具治理、MCP 外部工具、trace/usage 可观测和本地 eval 回归。
```

英文 tagline 可以作为 GitHub repo description 备用：

```text
Production-style CodingAgent runtime core with context engineering, governed tool execution, MCP tools, trace, usage reporting, and eval regression.
```

中文介绍：

```text
Agent Forge 是一个面向代码修改任务的 CodingAgent runtime core，聚焦上下文工程、工具治理、运行时控制、MCP 外部工具、trace/usage 可观测和本地 eval 回归。
```
