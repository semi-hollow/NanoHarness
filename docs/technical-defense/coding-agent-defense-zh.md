# CodingAgent 技术答辩笔记

这份文档用于准备技术追问。它不把项目包装成完整的 Codex/Claude Code
产品，而是准确描述它完成了什么闭环。

## 一句话定位

Agent Forge 是一个面向 SWE-bench 风格真实代码修复任务的 CodingAgent
Harness。它的重点不是 UI，而是 Agent 的工程控制面：上下文工程、工具治理、
执行控制、patch 生成、trace/replay、usage/cost 观测和 benchmark result card。

## 为什么不用自造 demo 证明效果

自造 demo 容易变成“我设计一个题，然后我的 agent 正好能做”。这对学习
AgentLoop 有帮助，但对效果证明不够可信。

现在项目主线换成 SWE-bench：

1. 任务来自真实 GitHub issue。
2. 仓库 checkout 到官方 base commit。
3. Agent 需要自己看代码、调用工具、生成 patch。
4. 输出 `predictions.jsonl`。
5. 可以交给官方 SWE-bench harness 判断是否 resolved。

这比 webhook/calculator 这种本地样例更能回答技术追问里的核心问题：

> 你的 CodingAgent 到底有没有解决真实代码问题的闭环？

## 为什么还保留少量 smoke

`scripts/verify.sh` 和 `examples/demo_repo` 只用于开发自检：

- Python 能不能 import。
- CLI 能不能启动。
- MockLLM 能不能跑通读文件、patch、命令执行、trace。

它们不是效果证明，不应该在项目介绍或答辩里当作核心证据。

## 架构怎么讲

可以按这条链讲：

```text
forge bench swebench
  -> load SWE-bench case
  -> checkout repo at base_commit
  -> AgentLoop
  -> ContextBuilder selects code context
  -> ModelGateway calls DeepSeek/OpenAI-compatible API
  -> ToolRouter/ToolRegistry execute read/grep/patch/run/git
  -> Safety layer checks path, command, permission
  -> TraceRecorder records every step
  -> git diff becomes predictions.jsonl
  -> official SWE-bench harness evaluates
  -> report.md summarizes result, usage, failure taxonomy
```

## 为什么需要 Agent，不是直接 prompt LLM

项目支持 `--direct-baseline`。它让模型只看 issue 文本并直接生成 patch。

AgentLoop 相比 direct baseline 的价值：

- 能逐步读文件，而不是猜代码结构。
- 能根据 observation 修正下一步动作。
- 能运行命令验证。
- 能在 trace 中解释为什么选了这些上下文。
- 能用 command policy 和 sandbox 控制风险。
- 能按 step 统计 token、cost、latency、tool success。

回答时不要说“Agent 一定更强”，而是说：

> 我用 baseline 对比来验证 AgentLoop 是否值得额外成本。Agent 的成本更高，
> 但它换来上下文可控、工具执行、失败恢复和可复盘证据。

## 技术追问

### 1. 你的项目是不是 toy？

以前如果只看 calculator/webhook，会像 toy。现在主证明换成 SWE-bench：

- 外部数据集。
- 真实 repo。
- base commit reproducibility。
- SWE-bench-compatible predictions。
- 可选官方 harness。
- result card 和 failure taxonomy。

所以项目不是靠自造样例自证，而是接入公开评测闭环。

### 2. 为什么不追求完整产品形态？

我的目标是研究 CodingAgent 核心控制面，而不是复制完整 IDE 产品。

Codex/Claude Code 还包括 TUI、IDE、账号、云执行、权限弹窗、任务队列等产品层。
这个项目刻意把重点放在：

- context engineering；
- tool governance；
- execution control；
- trace/replay；
- benchmark loop。

这更适合用来讨论 Agent 工程能力。

### 3. 如何证明结果可信？

分三层：

1. `patch_generated`：agent 是否生成 diff。
2. `official_eval_*`：SWE-bench harness 是否执行并判断。
3. trace/usage：解释为什么成功或失败。

如果没有官方 evaluation，我不会声称 resolved，只说生成了 candidate patch。

### 4. 如何做失败分析？

result card 里按这些类别看：

- `blocked`：provider、guardrail、预算、命令策略阻断。
- `no_patch`：上下文不足、模型没收敛、step budget 不够。
- `official_eval_failed`：patch 生成了但未通过官方测试。
- provider/config failure：API key、Docker、datasets、swebench 环境问题。

下一步优化不是盲目加 prompt，而是看失败归因：

- context 找错文件，改 retrieval/ranking；
- 工具调用失败，改 schema/command policy；
- 成本过高，改 context budget；
- patch 逻辑错，改 validation loop 或 baseline 对比。

### 5. 为什么现在不实现 multi-agent？

CodingAgent 的 P0 问题是单 agent 能否闭环 issue-to-patch。Multi-agent 如果没有
可验证收益，很容易只是 planner/coder/tester/reviewer 的线性包装。

所以这个项目当前不把 multi-agent 放进代码主线。主卖点是 SWE-bench 闭环和
可观测控制面。答辩中可以承认：如果未来要做 multi-agent，需要先定义清楚
任务分解、共享状态、冲突处理、子 agent 输出校验和收益指标，而不是为了多
agent 这个名词去堆模块。

## 项目表述

可以写：

> Built Agent Forge, a SWE-bench-oriented CodingAgent harness that turns real
> GitHub issues into reproducible patch predictions. Implemented context
> construction, provider-agnostic model gateway, sandboxed tool execution,
> trace/replay, token/cost usage reports, direct-LLM baseline comparison, and
> SWE-bench-compatible result cards.

中文讲法：

> 我做了一个面向 SWE-bench 真实代码修复任务的 CodingAgent Harness，不是只跑
> demo。它能加载 benchmark case，checkout 真实仓库 base commit，让 agent 自主
> 检索上下文、调用工具、生成 patch，并输出 predictions.jsonl、trace、usage 和
> result card。重点是把 CodingAgent 的效果验证闭环和工程控制面做出来。
