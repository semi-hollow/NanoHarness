# Architecture Whiteboard

```text
User Task -> CLI -> Supervisor/AgentLoop -> LLMClient
                                  ↓
                        ToolRegistry + Tools
                                  ↓
                Permission + Sandbox + Guardrails
                                  ↓
                        Observation -> Trace -> Eval
```

## 1分钟讲图
先讲执行主链，再讲安全拦截点，最后讲验证闭环。

## 3分钟讲图
补充 trade-off：MockLLM 保证离线可复现；allowlist 降低风险；multi-agent 提升分工但增加编排复杂度。

## 面试官常问
- 为什么不用 unrestricted bash？
- 多 agent 什么时候不值得？
- trace 和普通日志有什么差异？
