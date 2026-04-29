# Identity Creation Service Implementation Plan

## Goal
Build a reliable FastAPI-based Verus ID registration service from the current health-check baseline, with persistent job state, daemon routing by native coin, worker-based orchestration, and secure API/webhook flows.

## Delivery Principles
- Reliability first: no in-memory-only workflow for registration state.
- Non-blocking API: request handlers return quickly; long chain waits happen in a worker.
- Security by default: API key protection and signed webhooks.
- TDD-first execution using `uv` in every phase.

## TDD Workflow (Mandatory)
Use this loop for every feature:
1. Red: write/extend tests that express expected behavior.
2. Green: implement minimal code to pass tests.
3. Refactor: improve code structure while keeping tests green.

### `uv` Test Commands
- Install/sync dependencies: `uv sync`
- Add test deps if needed: `uv add --dev pytest pytest-cov httpx`
- Run full suite: `uv run pytest -q`
- Run one test file: `uv run pytest tests/test_registration_flow.py -q`
- Run one test case: `uv run pytest tests/test_registration_flow.py -k pending_rnc -q`
- Optional coverage gate: `uv run pytest --cov=. --cov-report=term-missing`

## Current Baseline in Repo
- API entrypoint and health endpoint: `id_create_service.py`
- RPC connection cache/manager: `rpc_manager.py`
- Env-driven daemon enablement: `SFConstants.py`
- Basic smoke test: `tests/test_smoke.py`

## Phase Plan

### Phase 1: Project Structure and Test Harness
Objective: establish test-first scaffolding for service, persistence, and worker layers.

Tasks:
- Create module structure:
	- `app/api/` (route handlers)
	- `app/core/` (config, auth, logging)
	- `app/db/` (sqlite access and schema)
	- `app/services/` (registration orchestration)
	- `app/worker/` (polling and state transitions)
- Add initial tests for config parsing and daemon resolution behavior.
- Keep `id_create_service.py` as wrapper while migrating logic into modules.

TDD acceptance:
- Tests for daemon resolution and enabled-daemon filtering written first.
- `uv run pytest -q` passes with new module layout.

### Phase 2: Persistence Layer (SQLite)
Objective: persist registration jobs and transitions.

Tasks:
- Add schema bootstrap script and repository methods.
- Create `registrations` table with fields:
	- `id`, `requested_name`, `parent_namespace`, `native_coin`, `daemon_name`
	- `primary_raddress`, `source_of_funds`
	- `status`, `rnc_txid`, `rnc_payload_json`, `idr_txid`
	- `webhook_url`, `webhook_secret`
	- `error_message`, `attempts`, `next_retry_at`
	- `created_at`, `updated_at`
- Add indexes on `status`, `next_retry_at`, `created_at`.

TDD acceptance:
- Repository tests cover create/read/update transitions and retries.
- Crash-safe behavior validated by reopening DB connection in tests.

### Phase 3: Registration API
Objective: implement request intake and immediate response.

Endpoints:
- `POST /api/register`
- `GET /api/status/{request_id}`
- optional `GET /api/registrations?status=...` for operations visibility.

Tasks:
- Validate request payload (`name`, `parent`, `native_coin`, `primary_raddress`, optional webhook data).
- Resolve daemon by ticker against enabled daemons only.
- Execute `registernamecommitment` only in API layer.
- Persist row as `pending_rnc_confirm` and return `202` with `request_id`.

TDD acceptance:
- API tests cover:
	- success path (`202`)
	- invalid ticker (`503`)
	- disabled daemon (`503`)
	- malformed payload (`422`)
	- status retrieval (`200`/`404`).

### Phase 4: Worker State Machine
Objective: move all wait/poll/next-step behavior into a separate process.

State transitions:
- `pending_rnc_confirm` -> `ready_for_idr` when `confirmations > 0`
- `ready_for_idr` -> `idr_submitted` when `registeridentity` broadcast succeeds
- `idr_submitted` -> `complete` when ID tx confirms
- Any step -> `failed` on non-retryable errors
- Transient errors -> retry with exponential backoff

Tasks:
- Implement worker loop (`uv run python worker.py`) with poll interval config.
- Enforce idempotency: do not rebroadcast when txid already exists.
- Add heartbeat/metrics log lines for operations.

TDD acceptance:
- Unit tests for each transition and retry logic.
- Integration test simulating restart and resume from DB state.

### Phase 5: Security
Objective: protect public-facing interfaces.

Tasks:
- Add API key requirement on write endpoints (`X-API-Key`).
- Support one or more keys from env.
- Add webhook signature header using HMAC SHA-256 (`X-Webhook-Signature`).
- Add webhook receiver verification example for downstream services.

TDD acceptance:
- API key tests: missing, invalid, valid.
- Webhook signing tests: deterministic signature, reject tampered payload.

### Phase 6: Webhook Delivery and Reliability
Objective: notify dependent systems consistently.

Tasks:
- Deliver webhook on `complete` and `failed` events.
- Retry on non-2xx responses with capped attempts and backoff.
- Persist delivery attempts and last error.

TDD acceptance:
- Tests for successful delivery and retry exhaustion.
- Idempotency test ensuring duplicate sends are not emitted for same terminal state.

### Phase 7: Ops and Deployment
Objective: run API + worker safely in dev and containerized environments.

Tasks:
- Add startup scripts/tasks for API and worker.
- Add Docker compose service for worker process.
- Document runbooks: replay failed jobs, rotate API keys, rotate webhook secrets.

TDD acceptance:
- Smoke tests for startup and health.
- CI executes test suite via `uv run pytest -q`.

## Suggested Initial Backlog (First Sprint)
1. Red: write tests for `POST /api/register` happy path and invalid ticker.
2. Green: implement DB schema + register endpoint with persistence.
3. Refactor: extract repository/service interfaces.
4. Red: write tests for worker transition `pending_rnc_confirm` -> `ready_for_idr`.
5. Green: implement worker polling and transition updates.
6. Refactor: add retry/backoff strategy class.
7. Red: write API key auth tests.
8. Green: add auth dependency to registration endpoint.

## Environment Variables to Introduce
- `REGISTRAR_DB_PATH` (default `./registrar.db`)
- `REGISTRAR_API_KEYS` (comma-separated keys)
- `WORKER_POLL_INTERVAL_SECONDS` (default `15`)
- `WORKER_MAX_RETRIES` (default `10`)
- `WEBHOOK_TIMEOUT_SECONDS` (default `5`)
- `HEALTH_RPC_DAEMON` (already present)

## Definition of Done (Service v1)
- API registration requests return quickly with durable request IDs.
- Worker completes registration flow without blocking API threads.
- Service survives restart with in-flight jobs intact.
- API key auth is enforced on registration endpoint.
- Webhooks are signed and retry-capable.
- Test suite is TDD-driven and green through `uv run pytest -q`.

## Working Cadence for This Repo
For each PR-sized change:
1. Add/adjust tests first (`uv run pytest ...` should fail).
2. Implement minimal code to pass tests.
3. Refactor and keep tests green.
4. Update docs (`README.md`) and env examples (`env.sample`).
5. Re-run full suite with `uv run pytest -q` before merge.
