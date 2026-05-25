def sample_payload(event_id: str = "evt_001", event_type: str = "payment.succeeded") -> dict:
    return {
        "event_id": event_id,
        "type": event_type,
        "secret": "test-secret",
        "data": {"amount": 100},
    }


def valid_headers() -> dict:
    return {"X-Webhook-Signature": "test-secret"}


def invalid_headers() -> dict:
    return {"X-Webhook-Signature": "bad-secret"}
