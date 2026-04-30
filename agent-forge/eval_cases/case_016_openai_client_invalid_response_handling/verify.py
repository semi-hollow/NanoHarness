from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient

resp = OpenAICompatibleLLMClient("http://localhost", "key", "model").parse_response({"choices": []})
raise SystemExit(0 if resp.error and resp.error["type"] == "invalid_response" else 1)
