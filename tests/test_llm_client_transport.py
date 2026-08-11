import http.client
import json
import unittest
from unittest.mock import patch

from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.llm_config import (
    DEEPSEEK_V4_CONTEXT_WINDOW,
    GLM_52_CONTEXT_WINDOW,
    LLMConfigRequest,
    OPENCODE_GO_BASE_URL,
    resolve_llm_config,
)


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

    def test_deepseek_v4_uses_provider_context_window_by_default(self):
        config = resolve_llm_config(
            LLMConfigRequest(
                provider="deepseek",
                api_key="test-key",
                model="deepseek-v4-flash",
            )
        )

        self.assertEqual(
            config.capabilities.context_window,
            DEEPSEEK_V4_CONTEXT_WINDOW,
        )
        self.assertEqual(
            config.capabilities.source,
            "provider_default:deepseek:deepseek-v4-flash",
        )

    def test_explicit_model_capabilities_override_provider_defaults(self):
        declared = ModelCapabilities(
            native_tool_calling=False,
            context_window=8_192,
            source="test-declaration",
        )

        config = resolve_llm_config(
            LLMConfigRequest(
                provider="deepseek",
                api_key="test-key",
                model="deepseek-v4-flash",
                capabilities=declared,
            )
        )

        self.assertIs(config.capabilities, declared)

    def test_opencode_go_defaults_to_chat_compatible_glm(self):
        with patch.dict(
            "os.environ",
            {"OPENCODE_GO_API_KEY": "test-go-key"},
            clear=True,
        ):
            config = resolve_llm_config(
                LLMConfigRequest(provider="opencode-go")
            )

        self.assertEqual(config.base_url, OPENCODE_GO_BASE_URL)
        self.assertEqual(config.model, "glm-5.2")
        self.assertEqual(config.api_key, "test-go-key")
        self.assertEqual(config.capabilities.context_window, GLM_52_CONTEXT_WINDOW)
        self.assertTrue(config.uses_openai_compatible_api)

    def test_opencode_go_rejects_models_requiring_an_unimplemented_transport(self):
        with self.assertRaisesRegex(ValueError, "Chat Completions adapter"):
            resolve_llm_config(
                LLMConfigRequest(
                    provider="opencode-go",
                    model="gpt-5.6-luna",
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
            captured["user_agent"] = request.get_header("User-agent")
            return Response()

        with patch("urllib.request.urlopen", side_effect=open_request):
            response = client.chat([], [])

        self.assertIsNone(response.error)
        self.assertEqual(captured["payload"]["temperature"], 0.25)
        self.assertNotIn("thinking", captured["payload"])
        self.assertNotIn("reasoning_effort", captured["payload"])
        self.assertEqual(captured["user_agent"], "NanoHarness/1.0")

    def test_thinking_request_sends_mode_and_effort_without_temperature(self):
        config = resolve_llm_config(
            LLMConfigRequest(
                provider="deepseek",
                api_key="test-key",
                model="deepseek-v4-pro",
                thinking_mode="enabled",
                reasoning_effort="max",
            )
        )
        client = OpenAICompatibleLLMClient.from_config(config)
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return (
                    b'{"choices":[{"message":{"content":"ok",'
                    b'"reasoning_content":"inspect then verify"}}]}'
                )

        def open_request(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return Response()

        messages = [
            Message(
                "assistant",
                None,
                reasoning_content="previous reasoning",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        },
                    }
                ],
            )
        ]
        with patch("urllib.request.urlopen", side_effect=open_request):
            response = client.chat(messages, TOOLS)

        payload = captured["payload"]
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertNotIn("temperature", payload)
        self.assertEqual(
            payload["messages"][0]["reasoning_content"],
            "previous reasoning",
        )
        self.assertEqual(response.reasoning_content, "inspect then verify")
        self.assertTrue(config.capabilities.reasoning_tokens)

    def test_reasoning_effort_cannot_be_combined_with_disabled_thinking(self):
        with self.assertRaisesRegex(ValueError, "requires thinking_mode"):
            resolve_llm_config(
                LLMConfigRequest(
                    provider="deepseek",
                    thinking_mode="disabled",
                    reasoning_effort="max",
                )
            )

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
