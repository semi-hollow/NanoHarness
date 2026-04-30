# Project Deep Dive Story

## 3分钟开场
1. Context + Role：我做的是 Agent Harness，不是聊天机器人。目标是回答：LLM 如何安全执行代码任务。  
2. Hardest Problem + Approach：难点是“能执行”与“可控”冲突。我用 permission/sandbox/guardrail + trace，先保安全和可观测。  
3. Result + Learning：single/multi demo、unittest、eval 全可复现；线上性能指标[待真实压测后补充]。

## Problem-driven 版本
我发现只会调用 OpenCode/Claude Code 不够，因为面试会追问底层机制与风险。于是我做最小闭环实现：AgentLoop、ToolRegistry、Sandbox、Approval、Trace、Eval。

## 4层追问
- What did you do? 我实现了从 task 到 tool-observation 的闭环。  
- Why this approach? 标准库优先、可复现优先，降低环境依赖。  
- What went wrong? patch 首次失败、命令拒绝、上下文不足。  
- What else considered? 真模型、多路路由、向量RAG，但放到 V2。
