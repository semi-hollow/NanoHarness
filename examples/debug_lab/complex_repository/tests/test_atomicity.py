"""完整回归才会暴露的失败原子性和币种边界。"""

from decimal import Decimal

import pytest

from settlement import (
    CaptureEvent,
    InMemorySettlementRepository,
    ReconciliationService,
    SettlementAccount,
)


def build_service():
    repository = InMemorySettlementRepository(
        [
            SettlementAccount(
                settlement_id="settlement-atomic",
                expected_amount=Decimal("100.00"),
                currency="USD",
            )
        ]
    )
    return ReconciliationService(repository), repository


def event(event_id: str, amount: str, currency: str = "USD") -> CaptureEvent:
    return CaptureEvent(
        provider="Adyen",
        event_id=event_id,
        settlement_id="settlement-atomic",
        amount=Decimal(amount),
        currency=currency,
    )


@pytest.mark.parametrize(
    ("bad_event", "message"),
    [
        (event("evt-over", "100.01"), "exceeds expected"),
        (event("evt-currency", "10.00", "EUR"), "unsupported currency"),
        (event("evt-mismatch", "10.00", "CNY"), "currency mismatch"),
    ],
)
def test_rejected_capture_has_no_persistent_side_effect(bad_event, message):
    service, repository = build_service()
    before = repository.snapshot()

    with pytest.raises(ValueError, match=message):
        service.apply_capture(bad_event)

    assert repository.snapshot() == before


def test_rejected_event_can_be_retried_with_corrected_payload():
    service, repository = build_service()
    bad = event("evt-retry", "120.00")

    with pytest.raises(ValueError, match="exceeds expected"):
        service.apply_capture(bad)

    corrected = service.apply_capture(event("evt-retry", "100.00"))
    assert corrected.outcome == "applied"
    assert len(repository.ledger_entries()) == 1
