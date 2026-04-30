# 17-architecture-whiteboard

Let me draw the architecture to make sure we are aligned.

```text
User Task/CLI
   ↓
Context Builder (repo map/memory/rag)
   ↓
Agent Runtime + LLM Client
   ↓
Tool Registry
   ↓
Permission + Sandbox + Guardrails
   ↓
Observation + Trace
   ↓
Eval + Report
```
