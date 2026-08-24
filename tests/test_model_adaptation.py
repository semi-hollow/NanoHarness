import unittest

from agent_forge.runtime.adapters.model_gateway import ModelGateway, RetryPolicy
from agent_forge.observability.domain.usage import build_usage_report
from agent_forge.runtime.domain.conversation import AgentResponse, Message
from agent_forge.runtime.adapters.openai_compatible import OpenAICompatibleLLMClient
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

    def test_records_provider_response_model_identity(self) -> None:
        response = self.client.parse_response(
            {
                "id": "response-1",
                "model": "provider/model-build-1",
                "choices": [{"message": {"content": "ok"}}],
            }
        )

        self.assertIsNone(response.error)
        self.assertEqual(response.observed_model, "provider/model-build-1")

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
        primary = SequenceModel([AgentResponse(None, [], {"code": "request_failed"})])
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
                            "observed_models": ["provider/model-build-1"],
                        },
                        "response_normalization": {},
                    }
                ],
            }
        )

        self.assertEqual(usage["summary"]["tool_call_repairs"], 1)
        self.assertEqual(
            usage["steps"][0]["llm_calls"][0]["provider_reported_models"],
            ["provider/model-build-1"],
        )

    def test_usage_counts_canonical_validation_evidence_only_once(self) -> None:
        events = []
        for step in range(1, 4):
            events.extend(
                [
                    {
                        "step": step,
                        "agent_name": "CodingAgent",
                        "event_type": "action",
                        "tool_call": "python_validation",
                    },
                    {
                        "step": step,
                        "agent_name": "CodingAgent",
                        "event_type": "validation_evidence",
                        "validation": {
                            "kind": "pytest",
                            "status": "failed",
                            "tool": "python_validation",
                        },
                    },
                    {
                        "step": step,
                        "agent_name": "CodingAgent",
                        "event_type": "tool_observation",
                        "success": False,
                        "execution_succeeded": True,
                    },
                    {
                        "step": step,
                        "agent_name": "CodingAgent",
                        "event_type": "tool_observation",
                        "tool_call": "python_validation",
                        "success": False,
                        "execution_succeeded": True,
                    },
                ]
            )

        usage = build_usage_report({"run_id": "run-validation", "events": events})

        self.assertEqual(usage["summary"]["failed_validations"], 3)
        self.assertEqual(
            usage["tool_efficiency"]["by_tool"]["python_validation"][
                "validation_failed"
            ],
            3,
        )

    def test_usage_keeps_observation_fallback_for_legacy_validation_trace(
        self,
    ) -> None:
        usage = build_usage_report(
            {
                "run_id": "legacy-run",
                "events": [
                    {
                        "step": 1,
                        "agent_name": "CodingAgent",
                        "event_type": "action",
                        "tool_call": "python_validation",
                    },
                    {
                        "step": 1,
                        "agent_name": "CodingAgent",
                        "event_type": "tool_observation",
                        "success": False,
                        "execution_succeeded": True,
                    },
                ],
            }
        )

        self.assertEqual(usage["summary"]["failed_validations"], 1)

    def test_gateway_prices_opencode_go_glm_usage(self) -> None:
        client = SequenceModel(
            [
                AgentResponse(
                    "done",
                    [],
                    usage={
                        "prompt_tokens": 1_000_000,
                        "completion_tokens": 100_000,
                        "total_tokens": 1_100_000,
                        "prompt_tokens_details": {"cached_tokens": 250_000},
                    },
                    observed_model="provider/glm-5.2-build-1",
                )
            ]
        )
        gateway = ModelGateway(
            client,
            provider="opencode-go",
            model="glm-5.2",
        )

        gateway.chat([Message("user", "task")], TOOLS)

        # 250K cached * $0.26/M + 750K miss * $1.40/M + 100K output * $4.40/M
        self.assertEqual(gateway.last_usage.estimated_cost_usd, 1.555)
        self.assertEqual(
            gateway.last_usage.observed_models,
            ["provider/glm-5.2-build-1"],
        )

    def test_gateway_preserves_response_identity_without_usage_payload(self) -> None:
        client = SequenceModel(
            [
                AgentResponse(
                    "done",
                    [],
                    response_id="response-without-usage",
                    observed_model="provider/model-build-1",
                )
            ]
        )
        gateway = ModelGateway(client, provider="provider", model="requested-model")

        gateway.chat([Message("user", "task")], TOOLS)

        self.assertEqual(gateway.last_usage.response_id, "response-without-usage")
        self.assertEqual(
            gateway.last_usage.observed_models,
            ["provider/model-build-1"],
        )


if __name__ == "__main__":
    unittest.main()
