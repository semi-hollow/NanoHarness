import unittest
import json
from unittest.mock import patch

from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient
from agent_forge.runtime.message import Message


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


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

    def test_openai_env_aliases(self):
        with patch.dict("os.environ", {
            "OPENAI_BASE_URL": "http://gateway",
            "OPENAI_API_KEY": "secret",
            "OPENAI_MODEL": "model-a",
        }, clear=True):
            client = OpenAICompatibleLLMClient.from_env()
        self.assertTrue(client.is_configured())
        self.assertEqual(client.base_url, "http://gateway")

    def test_no_env_returns_structured_error(self):
        with patch.dict("os.environ", {}, clear=True):
            client = OpenAICompatibleLLMClient.from_env()
            response = client.chat([Message("user", "hi")], [])
        self.assertFalse(client.is_configured())
        self.assertEqual(response.error["code"], "missing_config")

    def test_fake_http_content_response(self):
        payload = {"choices": [{"message": {"content": "final answer"}}]}
        with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(payload)):
            client = OpenAICompatibleLLMClient("http://gateway/v1", "key", "model")
            response = client.chat([Message("user", "hi")], [])
        self.assertEqual(response.content, "final answer")
        self.assertEqual(response.tool_calls, [])
        self.assertIsNone(response.error)

    def test_fake_http_tool_call_response(self):
        payload = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "grep", "arguments": "{\"keyword\":\"x\"}"},
                    }],
                }
            }]
        }
        with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(payload)):
            client = OpenAICompatibleLLMClient("http://gateway/v1", "key", "model")
            response = client.chat([Message("user", "search")], [{"name": "grep", "arguments": {"keyword": "str"}}])
        self.assertIsNone(response.error)
        self.assertEqual(response.tool_calls[0].name, "grep")
        self.assertEqual(response.tool_calls[0].arguments["keyword"], "x")
