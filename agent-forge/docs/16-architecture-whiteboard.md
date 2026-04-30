# Architecture Whiteboard

```text
User Task
  ↓
CLI
  ↓
Supervisor / Agent Loop
  ↓
Context Builder ── Repo Map / Memory / RAG
  ↓
LLM Client
  ↓
Tool Registry
  ↓
Tools: read / grep / patch / bash / git
  ↓
Permission + Sandbox + Guardrails
  ↓
Observation
  ↓
Trace + Eval
```

## 1分钟讲图
从上到下讲输入、决策、执行、安全拦截、观测评估闭环。

## 3分钟讲图
再补充 trade-off：为什么 MockLLM、为什么 allowlist、为什么 workflow+agent 混用。

## 常见追问
- 为什么不用 unrestricted bash？
- 多 Agent 值不值？
- trace 和日志区别？

## 如何标 scope
明确我负责 runtime/tool/safety/eval，未做 UI/分布式。
