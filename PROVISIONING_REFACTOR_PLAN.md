# REDUNDANT (Historical): Provisioning Refactor Plan

This document is retained for historical migration context only.

Canonical current guide:
- `PROVISIONING_GUIDE.md`

---

# Provisioning Refactor Plan: Subprocess to Service Boundary

## Objective

Replace Python-to-Node subprocess invocation with a clean service-to-service integration while preserving protocol compatibility and existing API behavior.

## Success Criteria

1. No provisioning runtime path in idcreate uses subprocess execution.
2. Verus primitive serialization/parsing is handled by a standalone Node provisioning service.
3. Python provisioning flow calls Node over HTTP with bounded timeouts and typed error mapping.
4. Existing provisioning API behavior remains compatible for clients.
5. Challenge and status state is durable across restarts.

## Current State Summary

- Provisioning endpoints are mounted in idcreate.
- Core provisioning serialization/parsing currently runs through Node scripts launched by Python subprocess.
- Challenge state is currently in-memory.
- There are known protocol and wiring issues to fix during migration.

## Migration Phases

## Phase 1: Stabilize with an Adapter Boundary

Goal: prepare code for migration without changing behavior.

Tasks:
1. Introduce a provisioning adapter interface in Python with methods:
   - build_challenge
   - verify_request
   - build_success_response
   - build_failure_response
   - base58check_encode
   - base58check_decode
2. Move current subprocess code behind a SubprocessProvisioningAdapter implementation.
3. Update provisioning engine to depend on adapter interface only.
4. Add golden tests that lock current JSON and hex outputs.

Exit criteria:
1. Existing tests pass with no API behavior change.
2. Subprocess usage exists only in one adapter module.

## Phase 2: Freeze a Versioned Contract

Goal: define stable communication between Python and Node services.

Tasks:
1. Define versioned endpoints under /v1:
   - POST /v1/provisioning/challenge/build
   - POST /v1/provisioning/request/verify
   - POST /v1/provisioning/response/build
   - POST /v1/base58check/encode
   - POST /v1/base58check/decode
2. Define request and response JSON schemas with examples.
3. Define error model:
   - code
   - message
   - details
4. Define timeout and retry semantics.

Exit criteria:
1. Contract document reviewed and frozen.
2. Shared fixture payloads exist for both Python and Node test suites.

## Phase 3: Build Standalone Node Provisioning Service

Goal: run primitives logic as an independent service.

Tasks:
1. Create a dedicated service project (recommended: svc-provisioning).
2. Port logic from script-based entry points to HTTP handlers.
3. Add health and readiness endpoints.
4. Add structured logging and request IDs.
5. Pin verus-typescript-primitives using a stable dependency source (tag or commit), not local relative paths.

Exit criteria:
1. Service runs independently in local/dev.
2. Contract tests pass against Node HTTP endpoints.

## Phase 4: Swap Python Integration to HTTP Client

Goal: replace subprocess bridge with a proper client.

Tasks:
1. Implement ProvisioningHttpClient in Python with:
   - timeout budgets
   - retry policy for transient failures
   - status-code-aware error mapping
2. Replace adapter implementation in provisioning engine with HTTP adapter.
3. Keep a temporary feature flag fallback to subprocess only during rollout.
4. Remove direct NODE_BIN and subprocess dependencies from normal runtime path.

Exit criteria:
1. No runtime subprocess calls in provisioning flow when feature flag is enabled.
2. Tests pass for both success and failure branches.

## Phase 5: Correctness and Durability Hardening

Goal: fix known issues and production hardening gaps.

Tasks:
1. Persist challenge state in DB or Redis.
2. Correct primary_raddress extraction path in request flow.
3. Remove response-building protocol shortcuts and enforce consistent decision/request linkage.
4. Add replay protection and strict challenge expiry enforcement.
5. Add cleanup job for expired challenge artifacts.

Exit criteria:
1. Restart-safe status lookups and challenge handling.
2. Horizontal scale does not break challenge verification.
3. Protocol compatibility tests pass.

## Phase 6: Deployment and Cutover

Goal: safely move production traffic to service-based provisioning.

Tasks:
1. Add provisioning service to compose/deployment manifests.
2. Add idcreate configuration:
   - PROVISIONING_SERVICE_URL
   - PROVISIONING_TIMEOUT_SECONDS
   - PROVISIONING_RETRY_COUNT
   - PROVISIONING_USE_HTTP_ADAPTER
3. Rollout strategy:
   - staging validation
   - canary traffic
   - full cutover
4. Remove obsolete subprocess scripts and adapter after stabilization window.

Exit criteria:
1. Full traffic uses HTTP-based provisioning integration.
2. Subprocess adapter is removed from production code path.

## Testing Strategy

## Unit Tests

1. Python adapter/client error mapping and retries.
2. Router behavior for challenge, request submission, and status.
3. State persistence logic.

## Contract Tests

1. Shared fixed vectors for challenge/request/response JSON and hex.
2. Cross-language verification against frozen fixtures.

## End-to-End Tests

1. idcreate + provisioning service + mocked RPC success flow.
2. idcreate + provisioning service failure and timeout flows.
3. Restart scenario validating persistent challenge continuity.

## Non-Functional Tests

1. Latency and timeout behavior under load.
2. Retry amplification and backoff behavior.
3. Failure injection for downstream service outages.

## Known Risks and Mitigations

1. Binary/hex compatibility drift.
   - Mitigation: golden fixtures and strict contract tests.
2. Decision/request linkage regressions.
   - Mitigation: explicit invariants and validation checks in both services.
3. Dependency breakage for primitives.
   - Mitigation: pin exact dependency revision and lockfile review.
4. Added network hop latency.
   - Mitigation: keep payloads compact and use strict timeout/retry budgets.

## Work Breakdown Recommendation

1. PR 1: Adapter boundary only, no behavior changes.
2. PR 2: Contract spec plus fixture corpus.
3. PR 3: New standalone Node provisioning service.
4. PR 4: Python HTTP client integration behind feature flag.
5. PR 5: Durability and protocol fixes.
6. PR 6: Cutover, cleanup, and subprocess removal.

## Immediate Next Action

Implement PR 1 to isolate subprocess calls behind a single adapter boundary, then freeze fixtures before any protocol behavior changes.

## Progress Snapshot (2026-04-02)

Completed:
1. Adapter boundary implemented with subprocess and HTTP adapters.
2. HTTP adapter selection via feature flag with subprocess fallback.
3. Contract harness and golden vectors added for challenge/request/response paths.
4. Minimal `svc-provisioning` HTTP service scaffolded and containerized.
5. One-command default HTTP integration test flow added.
6. Durable challenge state persisted in SQLite.
7. Replay protection enforced (challenge can only be consumed once).
8. Response serialization made deterministic; temporary fallback removed.
9. Debug logging added across provisioning router, engine, and svc-provisioning.

Remaining:
1. Formal API contract artifact (OpenAPI/JSON schema) for `/v1` routes.
2. Final retirement of subprocess adapter path after stabilization window.

Phase 6 rollout scripts delivered:
1. `scripts/provisioning_phase6_staging_check.sh`
2. `scripts/provisioning_phase6_canary_check.sh`
3. `scripts/provisioning_phase6_full_cutover.sh`
