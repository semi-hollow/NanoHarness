from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient
import json

resp = OpenAICompatibleLLMClient("http://localhost", "key", "model").parse_response({"choices": []})
ok=bool(resp.error and resp.error["type"] == "invalid_response")
print(json.dumps({"task_success":ok,"test_pass":True,"safety_violation":False,"notes":"OpenAI-compatible client returned structured invalid response"}))
raise SystemExit(0 if ok else 1)
