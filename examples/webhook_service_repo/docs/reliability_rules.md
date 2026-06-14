# Webhook Reliability Rules

Webhook providers may retry delivery.

The service must be idempotent by `event_id`.

Duplicate delivery should not create duplicate event records or duplicate jobs.

Duplicate delivery should still return HTTP 200 so the provider stops retrying.
