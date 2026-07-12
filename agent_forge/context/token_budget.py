def truncate(text: str, max_chars: int = 2000) -> str:
    """Cap context text so one large repo map cannot dominate the prompt."""

    return text if len(text) <= max_chars else text[:max_chars] + "\n[truncated]"


def truncate_middle(text: str, max_chars: int = 2000) -> str:
    """Keep the beginning and end when compressing code or observations.

    For coding agents, the tail often contains recent errors or final function
    definitions, while the head often contains imports and declarations. Middle
    truncation preserves both anchors better than blindly chopping the tail.
    """

    if len(text) <= max_chars:
        return text
    if max_chars < 80:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - len("\n[...middle truncated...]\n")
    return text[:head] + "\n[...middle truncated...]\n" + text[-tail:]
