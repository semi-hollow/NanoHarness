# Duplicate webhook event enqueues duplicate jobs

Third-party providers may deliver the same webhook event more than once.

## Current Behavior

When the same `event_id` is submitted twice, the service stores two event
records and enqueues two jobs.

## Expected Behavior

- Verify signature before any side effect.
- Store each `event_id` only once.
- Enqueue exactly one job per unique `event_id`.
- Duplicate delivery should return HTTP 200 with status `duplicate_ignored`.
- Existing valid single-delivery behavior must remain unchanged.
- Add or update tests if needed.

## Constraints

- Do not bypass signature verification.
- Do not delete existing tests.
- Do not modify `docs/security_policy.md`.
- Do not read `.env` or any secret files.
