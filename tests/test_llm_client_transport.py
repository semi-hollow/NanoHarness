import http.client
import json
import unittest
from unittest.mock import patch

from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.llm_config import LLMConfigRequest, resolve_llm_config


TOOLS = [
    {
        "name": "read_file",
        "description": "Read one file",
        "arguments": {"path": "str"},
    }
]


class LLMClientTransportTest(unittest.TestCase):
    def test_llm_config_rejects_temperature_outside_provider_contract(self):
        with self.assertRaisesRegex(ValueError, "temperature"):
            resolve_llm_config(
                LLMConfigRequest(
                    provider="openai-compatible",
                    temperature=2.1,
                )
            )

    def test_chat_sends_configured_temperature(self):
        client = OpenAICompatibleLLMClient(
            base_url="http://local.test/v1",
            api_key="test-key",
            model="test-model",
            temperature=0.25,
        )
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        def open_request(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Response()

        with patch("urllib.request.urlopen", side_effect=open_request):
            response = client.chat([], [])

        self.assertIsNone(response.error)
        self.assertEqual(captured["payload"]["temperature"], 0.25)

    def test_incomplete_read_becomes_structured_request_failure(self):
        client = OpenAICompatibleLLMClient(
            base_url="http://local.test/v1",
            api_key="test-key",
            model="test-model",
        )
        with patch(
            "urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"partial")
        ):
            response = client.chat([], [])

        self.assertIsNotNone(response.error)
        self.assertEqual(response.error["code"], "request_failed")
        self.assertIn("IncompleteRead", response.error["message"])

    def test_non_native_model_uses_bounded_text_tool_protocol(self):
        client = OpenAICompatibleLLMClient(
            base_url="http://local.test/v1",
            api_key="test-key",
            model="test-model",
            capabilities=ModelCapabilities(native_tool_calling=False),
        )
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return (
                    b'{"choices":[{"message":{"content":'
                    b'"{\\"name\\":\\"read_file\\",\\"arguments\\":'
                    b'{\\"path\\":\\"README.md\\"}}"}}]}'
                )

        def open_request(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return Response()

        with patch("urllib.request.urlopen", side_effect=open_request):
            response = client.chat([], TOOLS)

        self.assertNotIn("tools", captured["payload"])
        self.assertIn(
            "no native tool calling",
            captured["payload"]["messages"][-1]["content"],
        )
        self.assertEqual(response.tool_calls[0].name, "read_file")
        self.assertEqual(response.tool_calls[0].arguments, {"path": "README.md"})


if __name__ == "__main__":
    unittest.main()
