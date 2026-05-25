def verify_signature(payload: dict, headers: dict) -> bool:
    expected = payload.get("secret", "")
    provided = headers.get("X-Webhook-Signature", "")
    return bool(expected) and expected == provided
