from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_forge.bench.adapters.case_runtime import LocalCaseExecutor
from agent_forge.bench.application.swebench import _new_summary
from agent_forge.bench.domain.config import BenchRunLayout, SwebenchRunRequest
from agent_forge.bench.presentation.cli import _positive_int
from agent_forge.cli.parser import build_parser
from agent_forge.evaluation.domain.scorecard import build_scorecard
from agent_forge.runtime.domain.conversation import AgentResponse, ToolCall
from agent_forge.runtime.llm_config import LLMConfig
from agent_forge.runtime.wiring import build_llm
from scripts import probe_model_rate_limit_contract as rate_probe
from scripts import probe_model_tool_contract as tool_probe


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


def _probe_evidence(*, max_attempts: int) -> dict[str, object]:
    base_url = "https://opencode.ai/zen/go/v1"
    endpoint, digest = rate_probe._safe_endpoint_identity(base_url)
    return {
        "schema_version": 1,
        "status": "passed",
        "provider": "opencode-go",
        "requested_model": "glm-5.2",
        "credential_source": "OPENCODE_GO_API_KEY",
        "base_url_origin_path": endpoint,
        "base_url_sha256": digest,
        "thinking_mode": "enabled",
        "reasoning_effort": "high",
        "max_attempts": max_attempts,
        "tool_call_source": "native",
        "tool_call_count": 1,
        "tool_arguments_match": True,
        "round_trip_completed": True,
        "fallback_used": False,
        "attempts_per_call": [1, 1],
        "error_codes": [],
        "error_code": "",
        "observed_response_model": "provider-build-1",
        "round_trip_observed_response_model": "provider-build-1",
    }


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


class _Usage:
    def to_dict(self) -> dict[str, object]:
        return {
            "attempts": 1,
            "error_codes": [],
            "fallback_used": False,
            "total_tokens": 1,
        }


class _ProbeGateway:
    def __init__(self) -> None:
        self.last_usage = _Usage()
        self._calls = 0

    def chat(self, _messages: object, _tools: object) -> AgentResponse:
        self._calls += 1
        if self._calls == 1:
            return AgentResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="probe-1",
                        name="probe_read_file",
                        arguments={"path": "README.md"},
                    )
                ],
                normalization={"tool_call_source": "native"},
                observed_model="provider-build-1",
            )
        return AgentResponse(
            content="probe-complete",
            tool_calls=[],
            observed_model="provider-build-1",
        )


def test_tool_probe_passes_and_records_explicit_max_attempts(
    tmp_path: Path,
) -> None:
    output = tmp_path / "capability.json"
    config = _config()
    gateway = _ProbeGateway()
    argv = [
        "probe_model_tool_contract.py",
        "--provider",
        config.provider,
        "--model",
        config.model,
        "--base-url",
        config.base_url,
        "--max-attempts",
        "1",
        "--output",
        str(output),
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.object(tool_probe, "resolve_llm_config", return_value=config),
        patch.object(tool_probe, "build_llm", return_value=gateway) as builder,
    ):
        tool_probe.main()

    evidence = json.loads(output.read_text(encoding="utf-8"))
    builder.assert_called_once_with(config, max_attempts=1)
    assert evidence["max_attempts"] == 1
    assert evidence["attempts_per_call"] == [1, 1]


def test_rate_probe_forwards_and_records_explicit_max_attempts(
    tmp_path: Path,
) -> None:
    preflight = tmp_path / "capability.json"
    preflight.write_text(
        json.dumps(_probe_evidence(max_attempts=1)),
        encoding="utf-8",
    )
    output = tmp_path / "capacity.json"
    observed_argv: list[list[str]] = []

    def run_child(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        observed_argv.append(argv)
        assert argv[argv.index("--max-attempts") + 1] == "1"
        child_output = Path(argv[argv.index("--output") + 1])
        child_output.write_text(
            json.dumps(_probe_evidence(max_attempts=1)),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, "", "")

    argv = [
        "probe_model_rate_limit_contract.py",
        "--provider",
        "opencode-go",
        "--model",
        "glm-5.2",
        "--base-url",
        "https://opencode.ai/zen/go/v1",
        "--max-attempts",
        "1",
        "--round-trips",
        "2",
        "--capability-preflight",
        str(preflight),
        "--output",
        str(output),
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.object(rate_probe.subprocess, "run", side_effect=run_child),
    ):
        rate_probe.main()

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert len(observed_argv) == 2
    assert evidence["max_attempts"] == 1
    assert evidence["status"] == "passed"
