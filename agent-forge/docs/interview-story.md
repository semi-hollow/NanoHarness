# Interview Story

## One-minute Version

I built Agent Forge, a lightweight coding-agent harness that makes the core loop of modern coding agents explicit: context assembly, planning, tool selection, execution, observation, and evaluation. Instead of only wrapping an LLM API, I implemented the control layer around it: a tool registry, workspace-level safety guards, trace recording, mock and OpenAI-compatible LLM adapters, and a 16-case evaluation suite. The goal was not to compete with Claude Code or OpenCode, but to understand and demonstrate the engineering layer behind coding agents at a smaller scale.

## Three-minute Version

普通 chatbot 只能回答问题，但 coding agent 要真正修改代码，就需要上下文、工具、权限和反馈闭环。我做 Agent Forge 的目标，是把这个 control layer 拆成可以运行、可以测试、可以讲清楚的模块。

架构上，用户任务先经过 input guardrail，然后做 context assembly：repo map、memory、retrieval、symbol search、file ranking 和 budget report。Agent loop 每轮记录 plan summary，再调用 MockLLM 或 OpenAI-compatible LLM。如果 LLM 返回 tool call，系统会先做 permission check，再通过 ToolRegistry 执行工具。工具执行受 workspace sandbox 和 command policy 限制，结果统一变成 Observation 回到 loop。最后 trace JSON 和 summary.md 记录完整过程，eval runner 用 19 个 case 做回归。

我重点处理了几个 agent 常见问题：tool hallucination 用 unknown tool Observation 恢复；invalid arguments 用 registry 校验；context overflow 用 budget report 和 file ranker；无限循环用 max iteration、repeated tool call 和 failed tool stop condition；安全风险用 sandbox、permission 和 guardrail。项目结果不是线上业务指标，而是可复现证据：demo 三模式可跑，unittest 通过，eval 19/19 通过，trace 和 metrics 可审计。

## Hardest Problems

- 让项目既小又不空：代码不能复杂到像框架，也不能只是 README。
- 让 LLM 不可信假设落到工程边界：tool schema、registry、permission、sandbox 都要兜底。
- 让面试叙事有证据：每个 claims 都对应代码、测试、eval 或 trace。

## How I Measure It

- Demo：single/multi/workflow。
- Tests：`python3.11 -m unittest discover tests`。
- Eval：16 executable cases。
- Trace：JSON events + metrics + summary.md。
- Safety：secret file、network command、false test claim、unknown tool、invalid args。

## Common Follow-ups

1. Why not just use LangChain?  
   Because this project is meant to expose the control layer directly. Frameworks are useful, but I wanted to show I understand loop/tool/safety/eval from first principles.

2. How is this different from a chatbot?  
   A chatbot returns text; this harness can select tools, execute them, observe results, update state, and stop with trace evidence.

3. How do you prevent dangerous tool calls?  
   Tool calls go through permission policy, command policy, workspace sandbox, sensitive-file deny rules, and output guardrails.

4. What happens when the model selects the wrong tool?  
   Unknown tools and invalid arguments return failed Observations, which are traceable and can be used for recovery.

5. How would you scale it to production?  
   Add a model gateway, stronger schema validation, LSP provider, PR bot workflow, eval history, telemetry backend, and rollout gates.
