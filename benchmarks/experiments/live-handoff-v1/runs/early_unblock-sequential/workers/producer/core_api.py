def fetch_user(user_id: str, *, timeout_ms: int = 1000) -> dict:
    return {'id': user_id, 'timeout_ms': timeout_ms}
