# WebhookPatchBench

WebhookPatchBench is a tiny benchmark fixture for exercising a coding agent runtime.

It simulates a realistic webhook reliability bug without adding a web framework,
database, queue service, or external SDK. The point is not business complexity;
the point is whether the agent can read an issue, select the right files, patch
the handler, run tests, and leave trace/eval evidence.

## Scenario

A third-party provider can deliver the same webhook event more than once. The
service must verify the signature first, then ignore duplicate `event_id`
deliveries before any side effects.

Expected behavior:

- Valid first delivery returns `{"status": "accepted", "code": 200}`.
- Duplicate delivery returns `{"status": "duplicate_ignored", "code": 200}`.
- Invalid signature returns `{"status": "unauthorized", "code": 401}`.
- Each `event_id` is stored once and enqueued once.

## Run Tests

```bash
python -m unittest discover examples/webhook_service_repo/tests
```

The committed fixture starts with the duplicate-delivery bug. After the agent
patches `src/webhook_handler.py`, all tests should pass.
