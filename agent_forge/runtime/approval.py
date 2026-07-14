"""兼容导入：审批领域对象与 JSON Repository 已拆分。"""

from agent_forge.runtime.adapters.approval_json import (
    ApprovalStore,
    JsonApprovalRepository,
)
from agent_forge.runtime.domain.approval import ApprovalRequest

__all__ = ["ApprovalRequest", "ApprovalStore", "JsonApprovalRepository"]
