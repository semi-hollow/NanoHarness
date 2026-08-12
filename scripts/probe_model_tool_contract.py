#!/usr/bin/env python3
"""验证指定 provider/model 能完成一次原生工具往返。

本探测不执行请求的工具，也不写入凭据或模型自由文本；JSON 输出可用作
预检证据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from agent_forge.runtime.domain.conversation import AgentResponse, Message, ToolCall
from agent_forge.runtime.llm_config import LLMConfigRequest, resolve_llm_config
from agent_forge.runtime.wiring import build_llm


PROBE_TOOL = {
    "name": "probe_read_file",
    "description": "Read one file during a transport capability probe.",
    "arguments": {"path": "str"},
}


def _probe_passed(
    *,
    response: AgentResponse,
    tool_calls: list[ToolCall],
    fallback_used: bool,
) -> bool:
    """只核对运输契约和安全身份字段，不读取模型自由文本。"""

    normalization = response.normalization or {}
    return bool(
        response.error is None
        and len(tool_calls) == 1
        and tool_calls[0].name == "probe_read_file"
        and tool_calls[0].arguments == {"path": "README.md"}
        and normalization.get("tool_call_source") == "native"
        and response.observed_model
        and not fallback_used
    )


def _round_trip_passed(
    *,
    first_response: AgentResponse,
    final_response: AgentResponse,
    first_fallback_used: bool,
    final_fallback_used: bool,
) -> bool:
    """验证原生 ToolCall 可以带着 synthetic result 完成下一轮协议。"""

    return bool(
        _probe_passed(
            response=first_response,
            tool_calls=first_response.tool_calls,
            fallback_used=first_fallback_used,
        )
        and final_response.error is None
        and not final_response.tool_calls
        and (final_response.content or "").strip() == "probe-complete"
        and first_response.observed_model
        and final_response.observed_model == first_response.observed_model
        and not final_fallback_used
    )


def _safe_endpoint_identity(base_url: str) -> tuple[str, str]:
    """返回不含凭据、query 或 fragment 的 endpoint 身份与原串哈希。"""

    parsed = urlsplit(base_url)
    endpoint = f"{parsed.scheme}://{parsed.hostname or ''}"
    if parsed.port:
        endpoint += f":{parsed.port}"
    endpoint += parsed.path.rstrip("/")
    return endpoint, hashlib.sha256(base_url.encode("utf-8")).hexdigest()


def _positive_int(value: str) -> int:
    """解析正整数参数，防止探测隐式退化为零次尝试。"""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    """构造可单测的探测参数边界。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--thinking", default="enabled")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-attempts", type=_positive_int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    config = resolve_llm_config(
        LLMConfigRequest(
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            timeout=args.timeout,
            temperature=0.0,
            thinking_mode=args.thinking,
            reasoning_effort=args.reasoning_effort,
        )
    )
    if not config.is_configured():
        raise RuntimeError("provider configuration is incomplete")
    gateway = build_llm(config, max_attempts=args.max_attempts)
    response = gateway.chat(
        [
            Message(
                role="system",
                content=(
                    "This is a tool-transport capability check. Call the provided "
                    "tool exactly once. Do not answer with prose. After its result, "
                    "answer with the exact text probe-complete."
                ),
            ),
            Message(
                role="user",
                content="Call probe_read_file with path README.md now.",
            ),
        ],
        [PROBE_TOOL],
    )
    first_usage = gateway.last_usage.to_dict()
    calls = response.tool_calls
    normalization = response.normalization or {}
    final_response = AgentResponse(content=None, tool_calls=[])
    if _probe_passed(
        response=response,
        tool_calls=calls,
        fallback_used=bool(first_usage.get("fallback_used")),
    ):
        call = calls[0]
        final_response = gateway.chat(
            [
                Message(
                    role="system",
                    content=(
                        "This is a tool-transport capability check. Call the provided "
                        "tool exactly once. Do not answer with prose. After its result, "
                        "answer with the exact text probe-complete."
                    ),
                ),
                Message(
                    role="user",
                    content="Call probe_read_file with path README.md now.",
                ),
                Message(
                    role="assistant",
                    content="",
                    reasoning_content=response.reasoning_content,
                    tool_calls=[
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(
                                    call.arguments,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                ),
                Message(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.id,
                    content='{"path":"README.md","status":"synthetic_probe_ok"}',
                ),
            ],
            [PROBE_TOOL],
        )
    final_usage = gateway.last_usage.to_dict()
    passed = _round_trip_passed(
        first_response=response,
        final_response=final_response,
        first_fallback_used=bool(first_usage.get("fallback_used")),
        final_fallback_used=bool(final_usage.get("fallback_used")),
    )
    endpoint, base_url_sha256 = _safe_endpoint_identity(config.base_url)
    result = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "provider": config.provider,
        "requested_model": config.model,
        "credential_source": config.credential_source,
        "base_url_origin_path": endpoint,
        "base_url_sha256": base_url_sha256,
        "observed_response_model": response.observed_model,
        "round_trip_observed_response_model": final_response.observed_model,
        "capability_source": config.capabilities.source,
        "context_window": config.capabilities.context_window,
        "reasoning_tokens": config.capabilities.reasoning_tokens,
        "thinking_mode": config.thinking_mode,
        "reasoning_effort": config.reasoning_effort,
        "max_attempts": args.max_attempts,
        "tool_call_source": normalization.get("tool_call_source"),
        "tool_call_count": len(calls),
        "tool_name": calls[0].name if calls else None,
        "tool_arguments_match": bool(
            calls and calls[0].arguments == {"path": "README.md"}
        ),
        "round_trip_completed": bool(
            final_response.error is None
            and not final_response.tool_calls
            and (final_response.content or "").strip() == "probe-complete"
        ),
        "fallback_used": bool(
            first_usage.get("fallback_used") or final_usage.get("fallback_used")
        ),
        "attempts_per_call": [
            int(first_usage.get("attempts") or 0),
            int(final_usage.get("attempts") or 0),
        ],
        "error_codes": [
            *list(first_usage.get("error_codes") or []),
            *list(final_usage.get("error_codes") or []),
        ],
        "provider_usage_present": bool(
            int(first_usage.get("total_tokens") or 0)
            or int(final_usage.get("total_tokens") or 0)
        ),
        "error_code": str((response.error or {}).get("code") or ""),
        "boundary": (
            "The tool result was synthetic and no tool executed; credentials and "
            "model free text are omitted."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
