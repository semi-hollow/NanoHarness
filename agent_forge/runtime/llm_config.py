"""LLM provider 连接与采样配置的解析边界。"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_forge.runtime.domain.model import ModelCapabilities


DEFAULT_MODEL_CONTEXT_WINDOW = 32_768
DEEPSEEK_V4_CONTEXT_WINDOW = 1_000_000
GLM_52_CONTEXT_WINDOW = 1_000_000
KIMI_K27_CONTEXT_WINDOW = 262_144
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_DEFAULT_MODEL = "glm-5.2"

# 当前 Runtime 只实现 OpenAI-compatible Chat Completions。Go 中使用 Responses
# 或 Anthropic Messages 的模型必须先补对应 Adapter，不能假装与本协议兼容。
OPENCODE_GO_CHAT_COMPLETIONS_MODELS = frozenset(
    {
        "grok-4.5",
        "glm-5.2",
        "glm-5.1",
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.6",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "mimo-v2.5",
        "mimo-v2.5-pro",
        "hy3",
    }
)


# 核心数据：模型网关需要的 provider、凭据、模型和采样参数。
@dataclass
class LLMConfig:
    """解析完成、可以直接交给 ``OpenAICompatibleLLMClient`` 的配置。

    ``provider/base_url/api_key/model`` 标识远端；``timeout`` 控制单次 HTTP 调用；
    ``temperature`` 是非思考模式下的采样随机性；``thinking_mode`` 和
    ``reasoning_effort`` 决定 provider 是否返回独立推理内容以及推理预算。
    """

    provider: str = "deepseek"
    base_url: str = ""
    api_key: str = ""
    credential_source: str = ""
    model: str = ""
    timeout: int = 30
    temperature: float = 0.0
    thinking_mode: str = "auto"
    reasoning_effort: str | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)

    @property
    def uses_openai_compatible_api(self) -> bool:
        return self.provider in {
            "deepseek",
            "openai",
            "openai-compatible",
            "opencode-go",
        }

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


# 核心数据：解析模型连接配置时允许覆盖的来源与采样参数。
@dataclass(frozen=True)
class LLMConfigRequest:
    """显式值优先，随后依次读取 profile、环境变量和 provider 默认值。"""

    provider: str
    profile: str | None = None
    profile_file: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout: int = 30
    temperature: float | None = None
    thinking_mode: str | None = None
    reasoning_effort: str | None = None
    capabilities: ModelCapabilities | None = None


def load_llm_profile(
    profile_name: str, profile_file: str | None = None
) -> dict[str, Any]:
    candidates = []
    if profile_file:
        candidates.append(Path(profile_file))
    candidates.append(
        Path(os.getenv("AGENT_FORGE_LLM_PROFILE_FILE", "llm_profiles.json"))
    )

    for path in candidates:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles = data.get("profiles", data)
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            raise ValueError(f"LLM profile '{profile_name}' was not found in {path}")
        return profile

    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"LLM profile file not found. Searched: {searched}")


def resolve_llm_config(request: LLMConfigRequest) -> LLMConfig:
    profile_data: dict[str, Any] = {}
    resolved_provider = request.provider
    if request.profile:
        profile_data = load_llm_profile(request.profile, request.profile_file)
        resolved_provider = profile_data.get("provider", request.provider)

    deepseek_defaults = resolved_provider == "deepseek"
    opencode_go_defaults = resolved_provider == "opencode-go"
    default_base_url = (
        "https://api.deepseek.com"
        if deepseek_defaults
        else OPENCODE_GO_BASE_URL
        if opencode_go_defaults
        else ""
    )
    default_model = (
        "deepseek-v4-flash"
        if deepseek_defaults
        else OPENCODE_GO_DEFAULT_MODEL
        if opencode_go_defaults
        else ""
    )
    resolved_model = (
        request.model
        or profile_data.get("model")
        or os.getenv("AGENT_FORGE_MODEL")
        or os.getenv("OPENCODE_GO_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or os.getenv("OPENAI_MODEL")
        or default_model
        or ""
    )
    resolved_temperature = float(
        request.temperature
        if request.temperature is not None
        else profile_data.get(
            "temperature",
            os.getenv("AGENT_FORGE_TEMPERATURE", "0.0"),
        )
    )
    if not 0.0 <= resolved_temperature <= 2.0:
        raise ValueError("temperature must be between 0.0 and 2.0")
    resolved_thinking_mode = str(
        request.thinking_mode
        or profile_data.get("thinking_mode")
        or os.getenv("AGENT_FORGE_THINKING_MODE")
        or os.getenv("DEEPSEEK_THINKING_MODE")
        or "auto"
    ).lower()
    if resolved_thinking_mode not in {"auto", "enabled", "disabled"}:
        raise ValueError("thinking_mode must be auto, enabled, or disabled")
    resolved_reasoning_effort = (
        request.reasoning_effort
        or profile_data.get("reasoning_effort")
        or os.getenv("AGENT_FORGE_REASONING_EFFORT")
        or os.getenv("DEEPSEEK_REASONING_EFFORT")
        or None
    )
    if resolved_reasoning_effort is not None:
        resolved_reasoning_effort = str(resolved_reasoning_effort).lower()
        if resolved_reasoning_effort not in {"high", "max"}:
            raise ValueError("reasoning_effort must be high or max")
        if resolved_thinking_mode == "disabled":
            raise ValueError("reasoning_effort requires thinking_mode enabled or auto")
    if (
        opencode_go_defaults
        and resolved_model not in OPENCODE_GO_CHAT_COMPLETIONS_MODELS
    ):
        raise ValueError(
            "opencode-go model is not supported by the current Chat Completions "
            f"adapter: {resolved_model!r}"
        )

    resolved_api_key, credential_source = _resolve_api_key(
        provider=resolved_provider,
        explicit=request.api_key,
        profile_value=profile_data.get("api_key"),
    )
    return LLMConfig(
        provider=resolved_provider,
        base_url=(
            request.base_url
            or profile_data.get("base_url")
            or os.getenv("AGENT_FORGE_BASE_URL")
            or os.getenv("OPENCODE_GO_BASE_URL")
            or os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or default_base_url
            or ""
        ).rstrip("/"),
        api_key=resolved_api_key,
        credential_source=credential_source,
        model=resolved_model,
        timeout=int(profile_data.get("timeout", request.timeout)),
        temperature=resolved_temperature,
        thinking_mode=resolved_thinking_mode,
        reasoning_effort=resolved_reasoning_effort,
        capabilities=request.capabilities
        or default_model_capabilities(
            provider=resolved_provider,
            model=resolved_model,
            thinking_mode=resolved_thinking_mode,
        ),
    )


def _resolve_api_key(
    *,
    provider: str,
    explicit: str | None,
    profile_value: object,
) -> tuple[str, str]:
    """按 provider 隔离凭据来源，避免订阅端点静默借用另一服务的 Key。"""

    candidates: list[tuple[str, str | None]] = [
        ("explicit_request", explicit),
        ("profile", str(profile_value) if profile_value else None),
        ("AGENT_FORGE_API_KEY", os.getenv("AGENT_FORGE_API_KEY")),
    ]
    provider_environment = {
        "opencode-go": ("OPENCODE_GO_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "openai-compatible": ("OPENAI_API_KEY",),
    }
    candidates.extend(
        (name, os.getenv(name)) for name in provider_environment.get(provider, ())
    )
    for source, value in candidates:
        if value:
            return value, source
    return "", ""


def default_model_capabilities(
    *,
    provider: str,
    model: str,
    thinking_mode: str,
) -> ModelCapabilities:
    """根据已知 Provider 契约生成默认能力；调用方的显式声明仍优先。

    ``max_prompt_tokens`` 是 Runtime 愿意使用的策略预算，``context_window`` 是
    Provider 的物理上限。两者必须分开：把 DeepSeek V4 的能力误写为 32K，会让
    Runtime 即使配置了更大的预算也在 32K 提前压缩。
    """

    normalized_model = model.lower()
    is_deepseek_v4 = provider in {"deepseek", "opencode-go"} and (
        normalized_model.startswith("deepseek-v4")
    )
    known_context_windows = {
        "glm-5.2": GLM_52_CONTEXT_WINDOW,
        "kimi-k2.7-code": KIMI_K27_CONTEXT_WINDOW,
    }
    context_window = (
        DEEPSEEK_V4_CONTEXT_WINDOW
        if is_deepseek_v4
        else known_context_windows.get(normalized_model, DEFAULT_MODEL_CONTEXT_WINDOW)
    )
    opencode_go_reasoning_model = provider == "opencode-go" and normalized_model in {
        "glm-5.2",
        "kimi-k2.7-code",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }
    supports_reasoning = thinking_mode == "enabled" or (
        thinking_mode == "auto"
        and (
            "reasoner" in normalized_model
            or is_deepseek_v4
            or opencode_go_reasoning_model
        )
    )
    return ModelCapabilities(
        reasoning_tokens=supports_reasoning,
        context_window=context_window,
        source=f"provider_default:{provider}:{model or 'unknown'}",
    )
