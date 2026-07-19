"""模型与 Harness 协商的确定性能力描述。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.contracts import JsonObject


# 核心数据：Runtime 选择工具、上下文和降级策略时使用的模型能力矩阵。
@dataclass(frozen=True)
class ModelCapabilities:
    """描述模型协议能力，不描述主观质量。

    ``native_tool_calling`` 控制内置 OpenAI-compatible transport 使用原生 tools 还是
    受限 JSON 文本协议；``parallel_tool_calls`` 控制一轮可执行调用数；
    ``context_window`` 限制输入预算。其余字段是可观察的 Adapter 能力事实，当前核心
    Runtime 不会据此假装提供结构化输出、缓存或图像能力。
    """

    native_tool_calling: bool = True
    parallel_tool_calls: bool = True
    structured_output: bool = False
    reasoning_tokens: bool = False
    prompt_cache: bool = False
    context_window: int = 32_768
    supports_images: bool = False
    source: str = "runtime_default"

    def __post_init__(self) -> None:
        if self.context_window < 1_024:
            raise ValueError("model context_window must be at least 1024 tokens")

    def to_dict(self) -> JsonObject:
        """返回 trace/config artifact 使用的稳定表示。"""

        return {
            "native_tool_calling": self.native_tool_calling,
            "parallel_tool_calls": self.parallel_tool_calls,
            "structured_output": self.structured_output,
            "reasoning_tokens": self.reasoning_tokens,
            "prompt_cache": self.prompt_cache,
            "context_window": self.context_window,
            "supports_images": self.supports_images,
            "source": self.source,
        }
