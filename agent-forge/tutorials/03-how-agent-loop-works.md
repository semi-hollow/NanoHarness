# 03-how-agent-loop-works

## 1. 这篇解决什么问题
解释 Agent Loop 如何让模型和工具形成闭环，而不是一次性回答。

## 2. 先给结论
核心不是“模型更聪明”，而是“每一步可执行、可观测、可回退”。

## 3. 最小概念
- Message：用户/assistant/tool 消息。  
- ToolCall：结构化工具调用。  
- Observation：工具执行回传。  
- Guardrail：输入输出保护。

## 4. 对应代码在哪里
`agent_forge/runtime/agent_loop.py`、`agent_forge/runtime/llm_client.py`。

## 5. 运行一下看效果
`python run_demo.py --mode single`，你会看到第一次 patch 失败，再次 patch 成功。

## 6. 常见坑
只写固定脚本、不读取 observation 就无法做失败恢复。

## 7. 面试怎么说
我重点展示“failure recovery”：第一次 patch 失败，第二次修正，最后测试通过。

## 8. 下一步学什么
接真实模型时做 tool-call JSON 容错和重试策略。
