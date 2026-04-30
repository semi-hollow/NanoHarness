# 00-project-positioning

Agent Forge 是一个 Agent Harness，不是模型训练项目、不是 Claude 复制品、不是 OpenCode 配置包。

它用一个 coding-agent demo 复现 Agent 工程的核心控制层：

- LLM 如何提出 action；
- runtime 如何检查权限；
- tool 如何执行；
- observation 如何回传；
- trace 如何记录证据；
- eval 如何验证结果。

## 为什么适合面试

面试官通常不想听“我调了一个 API”，而是想看你是否理解 Agent 从 demo 到生产会遇到的问题：工具幻觉、上下文过载、越权执行、死循环、虚假成功、不可观测、不可评估。

本项目用小代码覆盖这些问题，并且每个点都有代码和测试证据。

## 当前边界

它不是生产 IDE Agent；真实模型、LSP、完整 MCP、CI runner 都是后续增强。这个边界必须主动说明，反而更可信。
