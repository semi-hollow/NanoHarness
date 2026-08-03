"""结算领域对象；不依赖文件、网络或测试框架。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SettlementStatus(str, Enum):
    """账户随累计入账金额推进的业务状态。"""

    OPEN = "open"
    PARTIAL = "partial"
    SETTLED = "settled"


@dataclass(frozen=True)
class CaptureEvent:
    """支付渠道发来的单次入账回调。"""

    provider: str
    event_id: str
    settlement_id: str
    amount: Decimal
    currency: str


@dataclass
class SettlementAccount:
    """一个待结算订单的累计状态。"""

    settlement_id: str
    expected_amount: Decimal
    currency: str
    captured_amount: Decimal = Decimal("0")
    status: SettlementStatus = SettlementStatus.OPEN


@dataclass(frozen=True)
class LedgerEntry:
    """成功受理事件产生的不可重复账本记录。"""

    operation_key: str
    settlement_id: str
    amount: Decimal
    currency: str
