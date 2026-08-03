"""复杂 Lab 的 focused tests：幂等、部分结算与最终状态。"""

from decimal import Decimal

from settlement import (
    CaptureEvent,
    InMemorySettlementRepository,
    ReconciliationService,
    SettlementAccount,
    SettlementStatus,
)


def build_service(expected_amount: str = "100.00"):
    repository = InMemorySettlementRepository(
        [
            SettlementAccount(
                settlement_id="settlement-1",
                expected_amount=Decimal(expected_amount),
                currency="USD",
            )
        ]
    )
    return ReconciliationService(repository), repository


def capture(event_id: str, amount: str, currency: str = "USD") -> CaptureEvent:
    return CaptureEvent(
        provider="Stripe",
        event_id=event_id,
        settlement_id="settlement-1",
        amount=Decimal(amount),
        currency=currency,
    )


def test_partial_then_final_capture_updates_state():
    service, repository = build_service()

    first = service.apply_capture(capture("evt-1", "39.995"))
    second = service.apply_capture(capture("evt-2", "60.00"))

    assert first.captured_amount == "40.00"
    assert first.status == SettlementStatus.PARTIAL
    assert second.captured_amount == "100.00"
    assert second.status == SettlementStatus.SETTLED
    assert len(repository.ledger_entries()) == 2


def test_provider_retry_is_idempotent_after_normalization():
    service, repository = build_service("25.00")

    applied = service.apply_capture(capture(" EVT-42 ", "25.00"))
    duplicate = service.apply_capture(
        CaptureEvent(
            provider=" stripe ",
            event_id="evt-42",
            settlement_id="settlement-1",
            amount=Decimal("25.00"),
            currency="USD",
        )
    )

    assert applied.outcome == "applied"
    assert duplicate.outcome == "duplicate"
    assert len(repository.ledger_entries()) == 1
