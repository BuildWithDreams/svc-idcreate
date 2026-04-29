# Storage Implementation Guide (Phased + TDD)

This is the execution playbook for implementing VerusID contentmultimap storage in this repository with a strict test-driven development workflow.

This guide assumes:
- Server-side sponsorship model (backend pays fees and controls wallet/RPC).
- Preferred storage path is Method 1 (`updateidentity` + nested `data` wrapper).
- We keep identity registration and storage flows separate but consistent.

## Delivery Principles

- TDD is mandatory: Red -> Green -> Refactor in every phase.
- Sequence safety first: one identity update per block chain state transition.
- TXID durability is non-negotiable: persist every write txid.
- Small, mergeable increments over large rewrites.

## Mandatory TDD Loop (`uv`)

For each story:
1. Red: write or extend tests first.
2. Green: implement the smallest change to pass.
3. Refactor: clean up while tests stay green.

Core commands:

```bash
uv sync
uv run pytest -q
uv run pytest tests/test_storage_rpc.py -q
uv run pytest tests/test_storage_service.py -q
uv run pytest tests/test_storage_worker.py -q
uv run pytest -k storage -q
```

If new testing deps are needed:

```bash
uv add --dev pytest pytest-cov
```

## Phase 0: Scope Lock + Test Skeleton

Goal:
- Lock MVP scope and create failing test skeletons before implementation.

Deliverables:
- New test files:
  - `tests/test_storage_rpc.py`
  - `tests/test_storage_service.py`
  - `tests/test_storage_worker.py`
- Test markers/fixtures for temporary files and deterministic chunk inputs.

Red tests to add first:
- `NodeRpc.update_identity` sends exact payload to RPC.
- Data wrapper is nested under `contentmultimap[vdxf_key][0].data`.
- Size routing rule chooses raw path for very small payloads and wrapper path otherwise.

Exit criteria:
- Failing tests clearly express desired behavior and naming.

## Phase 1: RPC Surface (Wrapper Layer)

Goal:
- Provide complete storage-related RPC methods in `NodeRpc`.

Methods:
- `update_identity(update_params)`
- `get_vdxf_id(key_name)`
- `get_identity_content(...)`
- `decrypt_data(payload)`
- `build_contentmultimap_data_wrapper(...)`

TDD tasks:
1. Red: write RPC proxy call/arg tests with mocks.
2. Green: implement methods and exception normalization.
3. Refactor: remove duplication in error handling.

Exit criteria:
- `tests/test_storage_rpc.py` passes.

## Phase 2: Persistence Model for Uploads

Goal:
- Add durable DB schema for uploads/chunks and txid tracking.

Tables:
- `storage_uploads`
- `storage_chunks`

Minimum fields:
- Upload: identity, status, file metadata, hash, chunk stats, error fields.
- Chunk: upload_id, chunk_index, vdxf_key, txid, status, error metadata.

TDD tasks:
1. Red: repo tests for create/read/update transition behavior.
2. Green: schema migration + repository helpers.
3. Refactor: index additions and query cleanup.

Exit criteria:
- Crash/restart persistence tests pass.
- Unique constraint on (`upload_id`, `chunk_index`) is enforced.

## Phase 3: Service API (Upload Lifecycle)

Goal:
- Add storage endpoints in FastAPI with auth and validation.

Endpoints (MVP):
- `POST /api/storage/upload`
- `GET /api/storage/upload/{upload_id}`
- `POST /api/storage/upload/{upload_id}/start`
- `POST /api/storage/upload/{upload_id}/retry`

Validation rules:
- `file_path` must exist and be inside allowed base directory.
- `chunk_size_bytes` bounded (`1` to `999000` for upload input target).
- explicit max upload limit from env.

TDD tasks:
1. Red: request validation and auth tests.
2. Green: endpoint handlers + DB writes.
3. Refactor: shared validation helpers.

Exit criteria:
- API tests cover `200/202/400/403/404/409/503` paths.

## Phase 4: Write Pipeline (Worker)

Goal:
- Sequentially write chunks to identity contentmultimap and persist txids.

Worker behavior:
1. Load next upload in writable state.
2. Pick next pending chunk by `chunk_index`.
3. Build `updateidentity` payload with nested `data` wrapper.
4. Submit RPC and record txid.
5. Wait for confirmation before next chunk.
6. Finalize metadata keys (`manifest`, `hash`, `chunkcount`, etc.).

Critical rule:
- Never process two chunks concurrently for the same identity.

TDD tasks:
1. Red: state machine transition tests (`pending -> uploading -> confirming -> complete`).
2. Green: worker implementation with retry/backoff.
3. Refactor: isolate transition logic into pure functions.

Exit criteria:
- Resume from partial progress after process restart is verified.

## Phase 5: Retrieval Pipeline

Goal:
- Retrieve and verify content from on-chain descriptors.

Retrieval flow:
1. Read chunk txids and keys from DB.
2. Call `getidentitycontent` per key.
3. Use descriptor index `0` for encrypted data references.
4. Call `decryptdata` with real txid and descriptor ivk.
5. Decode hex object data, reassemble by chunk order.
6. Verify SHA256 matches stored hash.

Endpoint:
- `GET /api/storage/retrieve/{upload_id}`

TDD tasks:
1. Red: retrieval happy path and descriptor-shape tests.
2. Green: implementation with deterministic ordering.
3. Refactor: extraction of decode/verify helpers.

Exit criteria:
- Byte-for-byte reconstruction tests pass.

## Phase 6: Namespace Thinking (Book-Centric Schema)

Goal:
- Align on structured keys for selective disclosure readiness.

Suggested key set under namespace:
- `clever::manifest`
- `clever::page.[N]`
- `clever::tx_map`

Implementation guidance:
- Manifest can be raw/hex if tiny.
- Page bodies should generally use data wrapper.
- Keep authoritative tx map in DB; optionally mirror on-chain.

TDD tasks:
1. Red: key mapping tests for deterministic key generation.
2. Green: helper implementation for map construction.
3. Refactor: include strict input normalization.

Exit criteria:
- Key map generation is deterministic and covered by tests.

## Phase 7: Hardening and Operations

Goal:
- Make production behavior observable and safe.

Additions:
- Structured logs with `upload_id`, `chunk_index`, `txid`.
- Retry policy tuning (transient vs permanent failures).
- Admin/ops visibility for failed uploads and requeues.
- Metrics counters (submitted, confirmed, failed, retries).

TDD tasks:
1. Red: retry exhaustion and permanent-failure tests.
2. Green: classify and handle failure types.
3. Refactor: centralize error mapping.

Exit criteria:
- Failure triage is test-covered and operationally visible.

## Phase 7 Open Hardening Map (Post-E2E)

Current status:
- Core storage pipeline is implemented and test-covered through Phase 6.
- The items below remain open to consider the storage subsystem production-hardened.

Priority P0 (must do before production traffic):
1. Structured storage logging
- Add consistent log fields for every storage transition: `upload_id`, `chunk_index`, `txid`, `state`, `attempts`, `daemon_name`.
- Include one start and one end log line for each `process_storage_upload_once` step.

2. Error classification matrix and normalization
- Centralize storage RPC error classification into explicit categories:
  - permanent precheck
  - transient transport/timeout
  - unknown
- Persist normalized error type alongside raw error text.

3. Retry exhaustion visibility
- Add API visibility endpoint for failed storage uploads with retry metadata.
- Ensure failed uploads include `attempts`, `next_retry_at`, and last error class.

4. Safety constraints verification
- Enforce and test path allowlist edge cases (symlinks and parent traversal).
- Enforce and test max upload size and chunk-size bounds from env.

Priority P1 (strongly recommended after initial integration):
1. Metrics counters
- Add counters for `storage_submitted`, `storage_confirmed`, `storage_failed`, `storage_retried`.
- Add one latency metric for upload completion time.

2. Operational runbook endpoints
- Add list/filter endpoint for storage uploads by status.
- Add bounded requeue-from-index endpoint for partial retry workflows.

3. Worker guardrails
- Add a sweep-level cap (max uploads processed per sweep) to avoid starvation.
- Add jitter support for retry scheduling to reduce retry bursts.

Priority P2 (quality and scale):
1. Retrieval response shape
- Add optional streaming/download mode to avoid large `content_hex` responses for large files.

2. Data lifecycle controls
- Add local temp-file retention policy and cleanup strategy after verification.

3. Extended integration tests
- Add vrsctest integration tests for medium and large multipart payloads with confirmation timing variance.

Suggested acceptance gates for “hardening complete”:
1. Storage failure triage API exists and is covered by tests.
2. Structured logs are emitted for submit, confirm, retry, and fail transitions.
3. Metrics are visible for submit/confirm/fail/retry counts.
4. Retry exhaustion and permanent-failure cases are covered by dedicated tests.
5. End-to-end test run includes at least one transient retry and one permanent failure scenario.

## Phase-by-Phase Command Cadence

Use this cadence for every phase:

```bash
# 1) Red
uv run pytest tests/<target_file>.py -q

# 2) Green
uv run pytest tests/<target_file>.py -q

# 3) Refactor validation
uv run pytest -q
```

Example focused run:

```bash
uv run pytest tests/test_storage_worker.py -k sequential -q
```

## Suggested Story Order (Small PRs)

1. RPC method tests and implementations.
2. Storage schema and repository tests.
3. Upload create/status/start endpoints.
4. Worker sequential write path for single chunk.
5. Multi-chunk progression with persisted txids.
6. Retrieval endpoint with hash verification.
7. Namespace key map helpers (`manifest/page/tx_map`).

## Done Definition (Storage MVP)

- Storage RPC methods exist and are test-covered.
- Upload records and chunk txids persist durably.
- Worker writes chunks sequentially and resumes after restart.
- Retrieval reconstructs bytes and verifies SHA256.
- Tests are green with `uv run pytest -q`.

## Risks to Watch Closely

- Silent truncation if raw mode is used above safe size.
- Missing txid persistence blocks future decrypt/retrieval.
- Parallel writes to one identity cause chain-state conflicts.
- Unbounded file paths introduce host filesystem risk.

## Immediate Next Task (Recommended)

Start with a tight vertical slice:
- One chunk upload (`<=999000` bytes), one txid, one retrieval verification.
- Drive it entirely with tests first.
- Expand to multi-chunk only after the single-chunk path is stable.
