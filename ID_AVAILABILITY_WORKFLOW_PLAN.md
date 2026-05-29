# ID Availability Workflow Plan

## Goal
Provide a first-class availability check endpoint so app UIs can validate candidate IDs before starting the async registration flow.

This supports both:
- Sub-IDs under a parent namespace (example: alice under bitcoins.vrsc)
- Root IDs (no parent provided)

## Locked Decisions
- `native_coin` is required for availability checks.
- Canonical identity strings must use trailing `@` for root IDs and parent namespaces.
- Availability checks are advisory only; `POST /api/register` remains the authoritative conflict gate.

## Why this is needed
Current registration already performs a pre-check and can return 409 when an identity exists, but frontends only learn this at submit time. A dedicated availability route enables immediate UX feedback and cleaner form flows.

## Proposed API Contract

### Endpoint
- Method: GET
- Path: /api/check-availability

Note: Keep the /api prefix for consistency with existing authenticated operational routes.

### Query Params
- name: string (required)
- parent: string | null (optional)
- native_coin: string (required)

Rationale: daemon selection in this service is native-coin based, so availability must resolve the same daemon as registration to avoid false positives/negatives across chains.

### Auth
- Require X-API-Key (same as POST /api/register)

### Response
- 200 OK
  - available: boolean
  - fully_qualified_name: string
  - reason: string | null

Semantics:
- available=true when identity is not found
- available=false with reason="Identity already exists" when identity is found

### Error handling
- 403 Invalid API key
- 400 Invalid query values (empty name, malformed parent)
- 503 when daemon cannot be resolved or node is unreachable/degraded
  - detail: "Identity node unreachable or degraded"

## Name Canonicalization Rules (must be explicit)
Implement one shared helper used by both availability and registration pre-check logic:

- Trim surrounding whitespace from `name` and `parent`.
- Reject empty values after trimming.
- Canonical parent normalization:
  - remove any trailing `@`, then append a single trailing `@`
  - examples:
    - `Verus Trading` -> `Verus Trading@`
    - `Verus Trading@` -> `Verus Trading@`
    - `Verus Trading@@` -> `Verus Trading@`
- Canonical identity string rules:
  - if `parent` is present: `<name>.<parent_canonical>`
  - if `parent` is omitted: `<name>@`
- Canonical output from the availability endpoint must always return `fully_qualified_name` in this canonical form.

Compatibility note:
- Existing register payloads currently use `parent` like `bitcoins.vrsc`.
- Keep API input flexible (accept with or without `@`) but normalize internally before RPC lookup and response serialization.
- Ensure registration pre-check and availability helper share the exact same canonicalization path.

## Backend Implementation Plan

### Phase 1: Contract and helper extraction
- Add AvailabilityResponse Pydantic model in id_create_service.py
- Add helper(s) in shared_functions.py or id_functions.py:
  - build_identity_fqn(name, parent)
  - classify_getidentity_not_found(error)
- Refactor existing registration pre-check to reuse helper (avoid duplicated, diverging logic)

### Phase 2: Endpoint implementation
- Add GET /api/check-availability route in id_create_service.py
- Resolve daemon via existing native_coin mapping helper
- Perform allowlist validation for parent namespace (same policy as registration)
- Call existing RPC connection and get_identity
- Map outcomes:
  - found -> available=false
  - not found -> available=true
  - infra/RPC degradation -> HTTP 503

### Phase 3: Client SDK updates
- Python client:
  - add check_identity_availability(name, native_coin, parent=None)
- TypeScript client:
  - add checkIdentityAvailability({ name, native_coin, parent? })
- Update client READMEs with check -> register example

### Phase 4: Documentation
- Update README Registration API section with availability endpoint contract
- Add curl examples for:
  - available sub-ID
  - available root-ID
  - already-taken response

## TDD / Regression Coverage (required)

### API tests
Add to tests/test_registration_api.py:
- returns available=true when get_identity throws not-found style error
- returns available=false when get_identity returns identity object
- returns 503 for daemon unresolved
- returns 503 for RPC degradation/non-not-found errors
- enforces API key (403)
- validates parent allowlist policy aligns with registration
- supports parent omitted for root-ID checks
- verifies canonical response format always uses trailing `@` rules

### Regression tests for shared pre-check behavior
- registration still returns 409 when identity exists
- registration still proceeds when identity is not found
- registration and availability produce same fully qualified identity string for same inputs

### Client tests
- tests/test_python_client.py:
  - check_identity_availability path/query/header assertions
  - error mapping for 503 and transport failures

TypeScript client tests are not currently present; add minimal tests or at least one executable example script assertion for checkIdentityAvailability.

## Prioritized Ticket Breakdown

### Ticket 1: Backend helpers and canonicalization
- Description: Implement shared identity normalization and not-found RPC classification used by both availability and registration pre-checks.
- Tasks:
  - Add `AvailabilityResponse` model in `id_create_service.py`.
  - Add `build_identity_fqn(name: str, parent: str | None) -> str` with strict trailing `@` canonicalization.
  - Add helper to classify "identity not found" JSON-RPC errors (prefer code-based matching, fallback to safe text matching).
- Effort: 1-2 hours
- Dependencies: None

### Ticket 2: Backend availability endpoint
- Description: Add `GET /api/check-availability` using shared helpers.
- Tasks:
  - Require `X-API-Key` and `native_coin`.
  - Resolve daemon from `native_coin` and reject unresolved chains with `503`.
  - Run parent allowlist validation consistent with registration rules.
  - Map outcomes to `{ available, fully_qualified_name, reason }`.
  - Refactor registration pre-check to use shared helper logic.
- Effort: 2-3 hours
- Dependencies: Ticket 1

### Ticket 3: Testing and regressions
- Description: Add endpoint and shared-helper regression coverage.
- Tasks:
  - Add availability endpoint tests in `tests/test_registration_api.py` for available, taken, degraded, auth, and allowlist behavior.
  - Add canonicalization tests for root and sub-ID formatting (with and without provided `@`).
  - Verify registration still returns `409` on existing identity.
- Effort: 2 hours
- Dependencies: Ticket 2

### Ticket 4: SDK updates
- Description: Expose check-availability in Python and TypeScript clients.
- Tasks:
  - Python: add `check_identity_availability(name, native_coin, parent=None)`.
  - TypeScript: add `checkIdentityAvailability({ name, native_coin, parent? })`.
  - Update both client READMEs with check-then-register snippets.
- Effort: 2 hours
- Dependencies: Ticket 2

### Ticket 5: Frontend/BFF integration
- Description: Integrate availability checks into provisioning UX.
- Tasks:
  - Add BFF passthrough route for `check-availability`.
  - Debounce input (300-500 ms) and render available/taken/degraded states.
  - Keep registration submit fallback handling for race-condition `409`.
- Effort: 3 hours
- Dependencies: Ticket 4

## Frontend Workflow Handoff

### UX flow
1. User enters name and optional parent.
2. UI debounces availability call (300-500 ms) after input stabilizes.
3. UI displays:
   - Available (green) when available=true
   - Already taken (warning) when available=false
   - Service unavailable (neutral error) for 503
4. On submit, call POST /api/register.
5. If submit returns 409 despite prior available=true, treat as race condition and show "Name was just taken".
6. After 202 response, poll GET /api/status/{request_id} until terminal status.

### Recommended FE state model
- idle
- checking
- available
- unavailable
- degraded
- submitting
- submitted_pending
- terminal_complete
- terminal_failed

### Race-condition policy
Availability is advisory, not a lock. Backend 409 on registration is authoritative.

### Polling guidance after registration
- poll every 3-5 seconds
- stop on complete/failed
- use timeout budget (for example 2-5 minutes depending on UX)

## Rollout Plan

### Step 1
Ship endpoint + tests + docs behind normal release (no feature flag required).

### Step 2
Ship client SDK methods.

### Step 3
Frontend adopts check-before-register flow and keeps 409 fallback handling.

### Step 4
Observe logs and status code distribution for one release cycle:
- check-availability 200 available=true vs false ratio
- check-availability 503 rate
- register 409 rate (should trend down after FE adoption)

## Acceptance Criteria
- FE can check candidate ID availability via one authenticated GET endpoint.
- Endpoint supports both sub-ID and root-ID requests.
- Registration behavior remains unchanged and backward compatible.
- Automated tests cover happy path, taken path, degraded path, auth, and race-condition fallback.
- Python and TypeScript clients expose availability methods.
- README documents the new endpoint and expected FE flow.
