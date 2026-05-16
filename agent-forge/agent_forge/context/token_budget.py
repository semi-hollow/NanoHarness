def truncate(text, max_chars=2000):
    """Cap context text so one large repo map cannot dominate the prompt."""

    return text if len(text) <= max_chars else text[:max_chars] + "\n[truncated]"
