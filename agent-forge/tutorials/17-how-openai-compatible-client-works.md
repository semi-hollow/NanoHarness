# 17-how-openai-compatible-client-works

V2 默认仍用 MockLLM，所以离线 demo 不受影响。真实模型路径通过 `OpenAICompatibleLLMClient` 接入。

学习顺序：

1. 读 `agent_forge/runtime/llm_client.py` 的 `AgentResponse`。
2. 看 `OpenAICompatibleLLMClient.from_env()` 如何读取环境变量。
3. 看 `parse_response()` 如何把 provider response 转成 `ToolCall`。
4. 跑 `python3.11 -m unittest tests.test_openai_compatible_llm`。

重点理解：invalid response 不应该炸掉整个 agent loop，而应该变成结构化错误，方便 fallback、trace 和 eval。
