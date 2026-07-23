import unittest

from agent_forge.models.gateway import ModelGateway, RetryPolicy
from agent_forge.observability.domain.usage import build_usage_report
from agent_forge.runtime.domain.conversation import AgentResponse, Message
from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient
from tests.support import SequenceModel


TOOLS = [
    {
        "name": "read_file",
        "description": "Read one file",
        "arguments": {"path": "str"},
    }
]


class ModelAdaptationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OpenAICompatibleLLMClient(
            base_url="http://unused",
            api_key="test",
            model="test-model",
        )

    def test_repairs_python_literal_tool_arguments_deterministically(self) -> None:
        response = self.client.parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{'path': 'target.py'}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            tools=TOOLS,
        )

        self.assertIsNone(response.error)
        self.assertEqual(response.tool_calls[0].arguments, {"path": "target.py"})
        self.assertIn(
            "read_file:python_literal_arguments_repaired",
            response.normalization["repairs"],
        )

    def test_promotes_exact_text_tool_call_only_for_visible_tool(self) -> None:
        response = self.client.parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"name":"read_file","arguments":{"path":"target.py"}}'
                        }
                    }
                ]
            },
            tools=TOOLS,
        )

        self.assertIsNone(response.error)
        self.assertIsNone(response.content)
        self.assertEqual(response.tool_calls[0].name, "read_file")
        self.assertEqual(
            response.normalization["tool_call_source"],
            "text_fallback",
        )

        unknown = self.client.parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"name":"delete_everything","arguments":{}}'
                        }
                    }
                ]
            },
            tools=TOOLS,
        )
        self.assertEqual(unknown.tool_calls, [])
        self.assertIn("delete_everything", unknown.content)

    def test_invalid_tool_arguments_return_repair_contract(self) -> None:
        response = self.client.parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "not-an-object",
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
            tools=TOOLS,
        )

        self.assertEqual(response.error["code"], "invalid_tool_call")
        self.assertIn("repair_prompt", response.error)

    def test_gateway_uses_repair_prompt_instead_of_repeating_same_request(self) -> None:
        client = SequenceModel(
            [
                AgentResponse(
                    None,
                    [],
                    {
                        "code": "invalid_tool_call",
                        "repair_prompt": "return valid JSON arguments",
                    },
                ),
                AgentResponse("repaired", []),
            ]
        )
        gateway = ModelGateway(
            client,
            retry_policy=RetryPolicy(max_attempts=2),
        )

        response = gateway.chat([Message("user", "read target.py")], TOOLS)

        self.assertIsNone(response.error)
        self.assertEqual(len(client.messages), 2)
        self.assertIn("return valid JSON", client.messages[1][-1].content)

    def test_gateway_does_not_blindly_retry_context_overflow(self) -> None:
        client = SequenceModel(
            [
                AgentResponse(
                    None,
                    [],
                    {
                        "code": "context_length_exceeded",
                        "message": "maximum context length exceeded",
                    },
                )
            ]
        )
        gateway = ModelGateway(
            client,
            retry_policy=RetryPolicy(max_attempts=3),
        )

        response = gateway.chat([Message("user", "long task")], TOOLS)

        self.assertEqual(response.error["code"], "context_length_exceeded")
        self.assertEqual(len(client.messages), 1)

    def test_gateway_does_not_send_context_overflow_to_fallback(self) -> None:
        primary = SequenceModel(
            [
                AgentResponse(
                    None,
                    [],
                    {"code": "context_length_exceeded"},
                )
            ]
        )
        fallback = SequenceModel([AgentResponse("should not run", [])])
        gateway = ModelGateway(
            primary,
            fallback=fallback,
            fallback_provider="backup",
            fallback_model="backup-model",
        )

        response = gateway.chat([Message("user", "long task")], TOOLS)

        self.assertEqual(response.error["code"], "context_length_exceeded")
        self.assertEqual(len(fallback.messages), 0)
        self.assertFalse(gateway.last_usage.fallback_used)

    def test_gateway_records_actual_fallback_model_identity(self) -> None:
        primary = SequenceModel(
            [AgentResponse(None, [], {"code": "request_failed"})]
        )
        fallback = SequenceModel([AgentResponse("recovered", [])])
        gateway = ModelGateway(
            primary,
            provider="primary",
            model="primary-model",
            fallback=fallback,
            fallback_provider="backup",
            fallback_model="backup-model",
        )

        response = gateway.chat([Message("user", "task")], TOOLS)

        self.assertEqual(response.content, "recovered")
        usage = gateway.last_usage.to_dict()
        self.assertTrue(usage["fallback_used"])
        self.assertEqual(usage["fallback_provider"], "backup")
        self.assertEqual(usage["fallback_model"], "backup-model")

    def test_http_error_classification_routes_overflow_to_runtime(self) -> None:
        classify = OpenAICompatibleLLMClient._classify_http_error

        self.assertEqual(
            classify(
                400,
                '{"error":{"code":"context_length_exceeded"}}',
            ),
            "context_length_exceeded",
        )
        self.assertEqual(classify(429, "rate limit"), "rate_limited")
        self.assertEqual(classify(503, "unavailable"), "server_error")

    def test_usage_counts_gateway_repair_retry_from_error_codes(self) -> None:
        usage = build_usage_report(
            {
                "run_id": "run-1",
                "events": [
                    {
                        "step": 1,
                        "agent_name": "CodingAgent",
                        "event_type": "llm_call",
                        "model_usage": {
                            "error_codes": ["invalid_tool_call"],
                        },
                        "response_normalization": {},
                    }
                ],
            }
        )

        self.assertEqual(usage["summary"]["tool_call_repairs"], 1)


if __name__ == "__main__":
    unittest.main()
