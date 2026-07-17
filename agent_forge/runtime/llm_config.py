"""LLM provider 连接与采样配置的解析边界。"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 核心数据：模型网关需要的 provider、凭据、模型和采样参数。
@dataclass
class LLMConfig:
    """解析完成、可以直接交给 ``OpenAICompatibleLLMClient`` 的配置。

    ``provider/base_url/api_key/model`` 标识远端；``timeout`` 控制单次 HTTP 调用；
    ``temperature`` 是实际发送且进入 benchmark experiment identity 的采样随机性。
    """

    provider: str = "deepseek"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: int = 30
    temperature: float = 0.0

    @property
    def uses_openai_compatible_api(self) -> bool:

        return self.provider in {"deepseek", "openai", "openai-compatible"}

    def is_configured(self) -> bool:

        return bool(self.base_url and self.api_key and self.model)


def load_llm_profile(profile_name: str, profile_file: str | None = None) -> dict[str, Any]:

    candidates = []
    if profile_file:
        candidates.append(Path(profile_file))
    candidates.append(Path(os.getenv("AGENT_FORGE_LLM_PROFILE_FILE", "llm_profiles.json")))

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


def resolve_llm_config(
    *,
    provider: str,
    profile: str | None = None,
    profile_file: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: int = 30,
    temperature: float | None = None,
) -> LLMConfig:

    profile_data: dict[str, Any] = {}
    resolved_provider = provider
    if profile:
        profile_data = load_llm_profile(profile, profile_file)
        resolved_provider = profile_data.get("provider", provider)

    deepseek_defaults = resolved_provider == "deepseek"
    default_base_url = "https://api.deepseek.com" if deepseek_defaults else ""
    default_model = "deepseek-v4-flash" if deepseek_defaults else ""
    resolved_temperature = float(
        temperature
        if temperature is not None
        else profile_data.get(
            "temperature",
            os.getenv("AGENT_FORGE_TEMPERATURE", "0.0"),
        )
    )
    if not 0.0 <= resolved_temperature <= 2.0:
        raise ValueError("temperature must be between 0.0 and 2.0")

    return LLMConfig(
        provider=resolved_provider,
        base_url=(
            base_url
            or profile_data.get("base_url")
            or os.getenv("AGENT_FORGE_BASE_URL")
            or os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or default_base_url
            or ""
        ).rstrip("/"),
        api_key=(
            api_key
            or profile_data.get("api_key")
            or os.getenv("AGENT_FORGE_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ),
        model=(
            model
            or profile_data.get("model")
            or os.getenv("AGENT_FORGE_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or os.getenv("OPENAI_MODEL")
            or default_model
            or ""
        ),
        timeout=int(profile_data.get("timeout", timeout)),
        temperature=resolved_temperature,
    )
