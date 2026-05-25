from .signature import verify_signature


def handle_webhook(payload: dict, headers: dict, store, queue) -> dict:
    if not verify_signature(payload, headers):
        return {"status": "unauthorized", "code": 401}

    event_id = payload["event_id"]
    event_type = payload["type"]

    # BUG: duplicate event_id is not checked before side effects.
    store.insert_event(event_id, event_type, payload)
    queue.enqueue(event_id, event_type)

    return {"status": "accepted", "code": 200}
