"""为长验证输出保留首尾证据，避免只保留通常噪声更重的前缀。"""

from __future__ import annotations


def render_output_window(output: str, *, max_chars: int) -> str:
    """返回带完整性元数据的有界输出；截断时同时保留 head 与 tail。"""

    normalized = output.strip()
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(normalized) <= max_chars:
        return (
            f"output_chars={len(normalized)} output_truncated=false\n{normalized}"
        ).rstrip()

    separator = "\n--- output tail ---\n"
    head_label = "--- output head ---\n"
    content_budget = max(0, max_chars - len(head_label) - len(separator))
    head_chars = content_budget * 2 // 3
    tail_chars = content_budget - head_chars
    head = normalized[:head_chars]
    tail = normalized[-tail_chars:] if tail_chars else ""
    return (
        f"output_chars={len(normalized)} output_truncated=true\n"
        f"{head_label}{head}{separator}{tail}"
    )
