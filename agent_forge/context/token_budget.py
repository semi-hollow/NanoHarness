def truncate(text: str, max_chars: int = 2000) -> str:

    return text if len(text) <= max_chars else text[:max_chars] + "\n[truncated]"


def truncate_middle(text: str, max_chars: int = 2000) -> str:

    if len(text) <= max_chars:
        return text
    if max_chars < 80:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - len("\n[...middle truncated...]\n")
    return text[:head] + "\n[...middle truncated...]\n" + text[-tail:]
