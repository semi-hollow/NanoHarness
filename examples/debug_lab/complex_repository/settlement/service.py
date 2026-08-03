"""渠道 Capture 回调的应用服务。"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import CaptureEvent, LedgerEntry, SettlementStatus
from .idempotency import canonical_event_key
from .money import normalize_amount
from .repository import InMemorySettlementRepository


@dataclass(frozen=True)
class ReconciliationResult:
    """一次回调处理后返回给上游的稳定结果。"""

    outcome: str
    captured_amount: str
    status: SettlementStatus


class ReconciliationService:
    """校验、幂等判断、入账和状态推进的应用服务。"""

    def __init__(self, repository: InMemorySettlementRepository) -> None:
        self.repository = repository

    def apply_capture(self, event: CaptureEvent) -> ReconciliationResult:
        """受理一条渠道入账回调，并返回最新结算状态。"""

        operation_key = canonical_event_key(event.provider, event.event_id)
        account = self.repository.get_account(event.settlement_id)
        if self.repository.was_processed(operation_key):
            return ReconciliationResult(
                outcome="duplicate",
                captured_amount=str(account.captured_amount),
                status=account.status,
            )

        # 当前顺序故意暴露练习缺陷：校验失败也可能提前污染幂等状态或账本。
        self.repository.mark_processed(operation_key)
        normalized_amount = normalize_amount(event.amount, event.currency)
        self.repository.append_ledger_entry(
            LedgerEntry(
                operation_key=operation_key,
                settlement_id=event.settlement_id,
                amount=normalized_amount,
                currency=event.currency,
            )
        )

        if event.currency.strip().upper() != account.currency:
            raise ValueError("currency mismatch")
        new_captured_amount = account.captured_amount + normalized_amount
        if new_captured_amount > account.expected_amount:
            raise ValueError("capture exceeds expected settlement amount")

        account.captured_amount = new_captured_amount
        account.status = (
            SettlementStatus.SETTLED
            if account.captured_amount >= account.expected_amount
            else SettlementStatus.PARTIAL
        )
        return ReconciliationResult(
            outcome="applied",
            captured_amount=str(account.captured_amount),
            status=account.status,
        )
