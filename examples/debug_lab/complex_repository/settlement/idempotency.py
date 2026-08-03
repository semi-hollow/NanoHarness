"""渠道回调幂等键生成规则。"""


def canonical_event_key(provider: str, event_id: str) -> str:
    """返回渠道事件键。

    当前实现保留了一个待修复问题：渠道和事件 ID 的格式差异会生成不同键。
    """

    return f"{provider}:{event_id}"
