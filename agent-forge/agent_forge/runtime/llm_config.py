import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LLMConfig:
    """Runtime LLM settings resolved from CLI flags, profiles, and env vars."""

    provider: str = "mock"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: int = 30

    @property
    def uses_openai_compatible_api(self) -> bool:
        return self.provider in {"openai", "openai-compatible"}

    def is_configured(self) -> bool:
        if self.provider == "mock":
            return True
        return bool(self.base_url and self.api_key and self.model)


def load_llm_profile(profile_name: str, profile_file: str | None = None) -> dict[str, Any]:
    """Load one named LLM profile from a local JSON file.

    The default file is intentionally local-first: users can keep secrets in
    `llm_profiles.json`, while `llm_profiles.example.json` documents the shape.
    """

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
) -> LLMConfig:
    """Resolve LLM settings with this priority: CLI flags > profile > env vars."""

    profile_data: dict[str, Any] = {}
    resolved_provider = provider
    if profile:
        profile_data = load_llm_profile(profile, profile_file)
        resolved_provider = profile_data.get("provider", provider)

    return LLMConfig(
        provider=resolved_provider,
        base_url=(
            base_url
            or profile_data.get("base_url")
            or os.getenv("AGENT_FORGE_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or ""
        ).rstrip("/"),
        api_key=(
            api_key
            or profile_data.get("api_key")
            or os.getenv("AGENT_FORGE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ),
        model=(
            model
            or profile_data.get("model")
            or os.getenv("AGENT_FORGE_MODEL")
            or os.getenv("OPENAI_MODEL")
            or ""
        ),
        timeout=int(profile_data.get("timeout", timeout)),
    )
