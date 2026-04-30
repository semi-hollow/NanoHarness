# 01-why-mock-llm

## 1. 这篇解决什么问题
解释为什么 Agent 项目默认用 MockLLM，而不是一上来接真实 API。

## 2. 先给结论
MockLLM 不是假装智能，而是让 harness 链路可重复、可测试、无 API key 也能演示。

## 3. 最小概念
MockLLM 固定返回工具调用序列：读源码、读测试、尝试 patch、观察失败、重试、跑测试、最终回答。

## 4. 对应代码在哪里
`agent_forge/runtime/llm_client.py` 的 `MockLLMClient`。

## 5. 运行一下看效果
`python3.11 run_demo.py --mode single`，trace 里每次 `llm_call` 都来自 MockLLM。

## 6. 常见坑
不要把 MockLLM 讲成模型能力；它验证的是 loop/tool/safety/trace 是否正确。

## 7. 面试怎么说
我先用 MockLLM 固定变量，证明控制层能跑通，再用 OpenAI-compatible client 接真实模型。

## 8. 下一步学什么
读 `18-how-context-v2-works.md` 或 `03-how-agent-loop-works.md`。
