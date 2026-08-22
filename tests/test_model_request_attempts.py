from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_forge.bench.adapters.case_runtime import LocalCaseExecutor
from agent_forge.bench.application.swebench import _new_summary
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.presentation.cli import _positive_int
from apps.cli.parser import build_parser
from agent_forge.evaluation.domain.scorecard import build_scorecard
from agent_forge.runtime.adapters.model_config import LLMConfig
from agent_forge.runtime.wiring import build_llm


def _config() -> LLMConfig:
    return LLMConfig(
        provider="opencode-go",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="test-key",
        credential_source="OPENCODE_GO_API_KEY",
        model="glm-5.2",
        thinking_mode="enabled",
        reasoning_effort="high",
    )


def test_build_llm_defaults_to_two_attempts_and_accepts_one() -> None:
    with patch(
        "agent_forge.runtime.wiring.OpenAICompatibleLLMClient.from_config",
        return_value=object(),
    ):
        default_gateway = build_llm(_config())
        single_attempt_gateway = build_llm(_config(), max_attempts=1)

    assert default_gateway.retry_policy.max_attempts == 2
    assert single_attempt_gateway.retry_policy.max_attempts == 1
    with pytest.raises(ValueError, match="max_attempts must be positive"):
        build_llm(_config(), max_attempts=0)


def test_benchmark_cli_request_summary_results_and_scorecard_preserve_attempts(
    tmp_path: Path,
) -> None:
    parser = build_parser()
    swebench_args = parser.parse_args(
        ["bench", "swebench", "--model-request-max-attempts", "1"]
    )
    campaign_args = parser.parse_args(
        ["bench", "campaign", "--model-request-max-attempts", "3"]
    )
    request = SwebenchRunRequest(model_request_max_attempts=1)
    summary = _new_summary(
        request,
        "run-attempts",
        BenchRunLayout(tmp_path, tmp_path / "predictions.jsonl"),
    )
    results = summary.to_dict()
    scorecard = build_scorecard(results, [])

    assert swebench_args.model_request_max_attempts == 1
    assert campaign_args.model_request_max_attempts == 3
    assert parser.parse_args(["bench", "swebench"]).model_request_max_attempts == 2
    assert request.model_request_max_attempts == 1
    assert summary.model_request_max_attempts == 1
    assert results["model_request_max_attempts"] == 1
    assert scorecard["metadata"]["model_request_max_attempts"] == 1
    with pytest.raises(ValueError, match="model_request_max_attempts must be positive"):
        SwebenchRunRequest(model_request_max_attempts=0)
    with pytest.raises(SystemExit):
        parser.parse_args(["bench", "swebench", "--model-request-max-attempts", "0"])
    with pytest.raises(ValueError):
        _positive_int("not-an-integer")


def test_case_runtime_passes_request_attempt_limit_to_gateway() -> None:
    config = _config()
    with (
        patch(
            "agent_forge.bench.adapters.case_runtime.resolve_llm_config",
            return_value=config,
        ),
        patch(
            "agent_forge.bench.adapters.case_runtime.build_llm",
            return_value=object(),
        ) as gateway_builder,
    ):
        LocalCaseExecutor._build_model(SwebenchRunRequest(model_request_max_attempts=1))

    gateway_builder.assert_called_once_with(config, max_attempts=1)
