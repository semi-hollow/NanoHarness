# 13-framework-comparison

本项目不试图替代成熟框架，而是用小代码解释核心机制。

| 系统 | 强项 | Agent Forge 的定位 |
| --- | --- | --- |
| Claude Code / Codex | 成熟 coding agent 产品，真实模型能力强，IDE/终端体验好 | 不复制产品，复现控制层：loop、tools、safety、trace、eval |
| OpenCode | 工程化 coding agent CLI，工具和上下文能力更完整 | 用标准库做可学习版本 |
| LangGraph | 状态图、持久化、多步骤 orchestration 强 | Agent Forge 保留 workflow vs agent 的最小对照 |
| OpenAI Agents SDK | provider 集成、tool calling、handoff 抽象成熟 | 本项目更适合学习底层边界 |
| Dify | 应用搭建和 workflow 编排友好 | Agent Forge 更偏代码级 harness |

## 为什么不用框架

面试项目要证明我理解机制，不只是会配置框架。所以 V2 先手写：

- AgentLoop；
- ToolRegistry；
- Permission/Sandbox；
- Trace；
- Eval。

## 当前缺什么

- 没有成熟 IDE integration；
- 没有完整 LSP；
- 没有完整 MCP protocol；
- 没有生产模型网关；
- 没有分布式 trace backend。

## 后续怎么增强

可以把当前模块逐步替换为成熟框架组件：用 LangGraph 承接状态机，用 OpenAI-compatible gateway 承接模型路由，用 LSP provider 承接符号级上下文。
