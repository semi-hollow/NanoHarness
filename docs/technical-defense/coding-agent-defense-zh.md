# CodingAgent 技术答辩笔记

这份文档用于准备技术追问。当前项目定位不是“展示一个概念”，而是一个能接入真实 LLM API、在真实仓库里干活的 CodingAgent harness。

## 一句话定位

Agent Forge 是一个面向真实代码仓库和 SWE-bench 风格任务的 CodingAgent harness。它的重点不是 IDE 外壳，而是 Agent 的工程控制面：上下文工程、runtime skills、工具治理、执行控制、patch 生成、trace/replay、usage/cost 观测和 benchmark result card。

## 真实使用主线

日常开发时用 `forge run`：

```text
forge run "<真实代码任务>" --provider deepseek
  -> AgentLoop
  -> SkillRegistry selects active coding skills
  -> ContextBuilder selects repo evidence
  -> ModelGateway calls DeepSeek/OpenAI-compatible API
  -> ToolRouter exposes task-relevant tools
  -> ToolRegistry executes read/grep/patch/run/git/MCP tools
  -> Safety layer checks path, command, permission
  -> TraceRecorder records every step
  -> usage_report.md explains token, cost, context, tool efficiency
```

效果闭环时用 `forge bench swebench`：

```text
forge bench swebench
  -> load SWE-bench case
  -> checkout repo at base_commit
  -> run the same AgentLoop
  -> git diff becomes predictions.jsonl
  -> optional official SWE-bench harness evaluates
  -> report.md summarizes result, usage, failure taxonomy
```

## 为什么不是直接 prompt LLM

项目支持 `--direct-baseline`。它让模型只看 issue 文本并直接生成 patch。

AgentLoop 相比 direct baseline 的价值：

- 能逐步读文件，而不是猜代码结构。
- 能根据 observation 修正下一步动作。
- 能运行命令验证。
- 能用 runtime skills 注入任务流程和完成标准。
- 能在 trace 中解释为什么选了这些上下文和工具。
- 能用 command policy 和 sandbox 控制风险。
- 能按 step 统计 token、cost、latency、tool success。

回答时不要说“Agent 一定更强”，而是说：

> 我用 baseline 对比来验证 AgentLoop 是否值得额外成本。Agent 的成本更高，但它换来上下文可控、工具执行、失败恢复和可复盘证据。

## 如何证明结果可信

分三层：

1. `patch_generated`：agent 是否生成 diff。
2. `official_eval_*`：SWE-bench harness 是否执行并判断。
3. trace/usage：解释为什么成功或失败。

如果没有官方 evaluation，不声称 resolved，只说生成了 candidate patch，并给出 trace、usage 和失败归因。

## 如何做失败分析

result card 里按这些类别看：

- `blocked`：provider、guardrail、预算、命令策略阻断。
- `no_patch`：上下文不足、模型没收敛、step budget 不够。
- `official_eval_failed`：patch 生成了但未通过官方测试。
- provider/config failure：API key、Docker、datasets、swebench 环境问题。

下一步优化不是盲目加 prompt，而是看失败归因：

- context 找错文件，改 retrieval/ranking。
- 工具调用失败，改 schema/command policy。
- 成本过高，改 context budget。
- patch 逻辑错，改 validation loop 或 baseline 对比。

## 为什么现在不实现 multi-agent 主线

CodingAgent 的 P0 问题是单 agent 能否闭环 issue-to-patch 和日常代码任务。Multi-agent 如果没有可验证收益，很容易只是 planner/coder/tester/reviewer 的线性包装。

所以这个项目当前不把 multi-agent 放进代码主线。主卖点是：

- 真实 LLM API。
- 真实仓库任务。
- runtime skills。
- 工具治理。
- 安全边界。
- SWE-bench 闭环。
- 可观测 trace/usage。

如果未来要做 multi-agent，需要先定义清楚任务分解、共享状态、冲突处理、子 agent 输出校验和收益指标，而不是为了多 agent 这个名词堆模块。

## 项目表述

英文：

> Built Agent Forge, a SWE-bench-oriented CodingAgent harness that turns real repository tasks into reproducible patch predictions. Implemented runtime skill selection, context construction, provider-agnostic model gateway, sandboxed tool execution, trace/replay, token/cost usage reports, direct-LLM baseline comparison, and SWE-bench-compatible result cards.

中文：

> 我做了一个面向真实代码仓库和 SWE-bench 风格任务的 CodingAgent harness。它接入 DeepSeek/OpenAI-compatible API，让 agent 自主检索上下文、调用受控工具、生成 patch，并输出 predictions.jsonl、trace、usage 和 result card。重点是把 CodingAgent 的真实使用路径、效果验证闭环和工程控制面做出来。
