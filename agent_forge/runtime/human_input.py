"""兼容导入：人工输入领域对象与 JSON Repository 已拆分。"""

from agent_forge.runtime.adapters.human_input_json import (
    HumanInputStore,
    JsonHumanInputRepository,
)
from agent_forge.runtime.domain.human_input import HumanInputRequest

__all__ = ["HumanInputRequest", "HumanInputStore", "JsonHumanInputRepository"]
