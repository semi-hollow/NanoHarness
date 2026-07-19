"""将可选模型声明归一化为 Runtime 可执行策略。"""

from __future__ import annotations

from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.ports import ModelPort


def resolve_model_capabilities(
    model: ModelPort,
    configured: ModelCapabilities | None,
    *,
    fallback_context_window: int,
) -> ModelCapabilities:
    """显式配置优先，其次读取模型对象声明，最后使用兼容默认值。"""

    if configured is not None:
        return configured
    declared = getattr(model, "capabilities", None)
    if isinstance(declared, ModelCapabilities):
        return declared
    return ModelCapabilities(
        context_window=max(1_024, fallback_context_window),
        source="legacy_model_port_default",
    )
