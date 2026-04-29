# Suggestions

This file captures practical, right-sized improvements for the current service phase.

## Priority 1: Low-effort, high-value

- Add structured logging fields everywhere possible:
  - `request_id`
  - `status`
  - `daemon_name`
  - `native_coin`
  - `txid_rnc`
  - `txid_idr`
- Add a startup log summary that prints key runtime config (without secrets):
  - DB path
  - enabled daemons
  - worker retry settings
  - webhook retry settings
- Add one endpoint for status counts:
  - Example: `GET /api/registrations/summary`
  - Return counts for `pending_rnc_confirm`, `ready_for_idr`, `idr_submitted`, `complete`, `failed`
- Add simple request validation hardening for registration payload:
  - sane name length
  - allowed name charset
  - parent format sanity check
- Add explicit index creation for common ops queries:
  - `(status, updated_at)`
  - `(webhook_delivered, webhook_next_retry_at)`

## Priority 2: Operational reliability

- Add a dead-letter style marker for permanently failed webhooks:
  - keep `status` as business status
  - include `webhook_attempts`, `webhook_last_error`
- Add a periodic cleanup task for old successful records:
  - archive or delete rows older than N days
  - keep failed rows longer for diagnosis
- Add idempotency key support on register endpoint:
  - optional header `Idempotency-Key`
  - prevent accidental duplicate name commitments from client retries
- Add worker lock protection for multi-runner safety:
  - if multiple cron jobs overlap, ensure one logical worker processes a row at a time
  - SQLite approach: atomic conditional updates per row state transition

## Priority 3: Security and controls

- Rotate API keys regularly and support key labels in logs (not raw keys).
- Restrict allowed webhook hosts/domains if possible to avoid SSRF risk.
- Add optional IP allowlist for admin/ops endpoints.
- Add request body size limits on API.
- Add a warning in docs: never log RPC credentials or webhook secrets.

## Priority 4: Monitoring and alerting

- Emit one log line per lifecycle transition:
  - from status
  - to status
  - request_id
  - reason
- Create simple alert thresholds:
  - too many failed registrations in 15m
  - too many webhook failures in 15m
  - queue growth (pending + ready + submitted)
- Add synthetic probe:
  - health endpoint per enabled coin
  - periodic check from monitoring system

## Priority 5: Developer experience

- Add Make-like task aliases in docs (or VS Code tasks):
  - test
  - run-api
  - run-worker-once
- Add test fixtures for common DB seeded states to reduce test duplication.
- Add one end-to-end test that simulates:
  - register request
  - worker transitions
  - webhook dispatch success

## Nice-to-have later

- Migrate from SQLite to Postgres when concurrency increases.
- Move worker to a long-running process manager (systemd/supervisor) instead of cron if throughput grows.
- Add OpenTelemetry traces for API and worker flow.

## Keep doing

- Continue TDD with `uv run pytest -q` before and after each change.
- Keep worker and API logic shareable but modular.
- Keep docs and curl examples updated as endpoints evolve.
