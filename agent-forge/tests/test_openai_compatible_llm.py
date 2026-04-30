import unittest

from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient


class TestOpenAICompatibleLLMClient(unittest.TestCase):
    def test_parse_tool_calls(self):
        client = OpenAICompatibleLLMClient("http://localhost", "key", "model")
        response = client.parse_response({
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                    }],
                }
            }]
        })
        self.assertIsNone(response.error)
        self.assertEqual(response.tool_calls[0].name, "read_file")
        self.assertEqual(response.tool_calls[0].arguments["path"], "README.md")

    def test_invalid_response_is_structured(self):
        client = OpenAICompatibleLLMClient("http://localhost", "key", "model")
        response = client.parse_response({"choices": []})
        self.assertEqual(response.error["type"], "invalid_response")
        self.assertEqual(response.error["code"], "missing_choices")
