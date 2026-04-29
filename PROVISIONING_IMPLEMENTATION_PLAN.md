# REDUNDANT (Historical): Provisioning Implementation Plan

This document is retained for historical design context only.

Canonical current guide:
- `PROVISIONING_GUIDE.md`

---

# Provisioning Endpoints Implementation Plan
# Extending svc-idcreate with VerusID Login Consent Provisioning

## Goal

Add VerusID provisioning endpoints to `svc-idcreate` following the Login Consent
Provisioning ceremony (challenge → wallet-signed request → verify → register on-chain →
response). The existing name-commitment registration flow (`POST /api/register`)
remains unchanged; provisioning is a separate interactive workflow.

---

## Context: How Provisioning Differs From Existing Registration

The existing `POST /api/register` flow is **non-interactive**:

1. Client POSTs name + parent + primary_raddress
2. Service calls `registernamecommitment` → returns `request_id`
3. Worker polls for confirmations and calls `registeridentity`
4. Webhook fires on terminal state

The provisioning flow is **interactive** (wallet-involved):

1. Service creates + signs a `ProvisioningChallenge` (name, system_id, parent, challenge_id)
2. Service returns the challenge (or encodes it in a deeplink URI)
3. **Wallet presents UI to user** ("Create identity `alice.VRSC`?")
4. User approves; wallet signs and returns `ProvisioningRequest`
5. Service verifies wallet signature against the challenge
6. Service creates identity on-chain (via existing `registeridentity` RPC)
7. Service returns `ProvisioningResponse` with result

Key primitives consumed from `verus-typescript-primitives`:

- `ProvisioningChallenge` — created + signed by service
- `ProvisioningRequest` — returned by wallet after user approval
- `ProvisioningResponse` — returned by service after processing
- `ProvisioningDecision` — embedded in response; carries result state
- `ProvisioningResult` — success/failure with txid, fully_qualified_name, etc.

---

## Architecture

```
svc-idcreate/
├── id_create_service.py          # FastAPI app (extend with provisioning routes)
├── provisioning/                 # NEW: provisioning-specific module
│   ├── __init__.py
│   ├── challenge.py              # ProvisioningChallenge creation + signing
│   ├── verification.py           # ProvisioningRequest signature verification
│   ├── response.py               # ProvisioningResponse / ProvisioningResult building
│   └── router.py                 # FastAPI router aggregating all provisioning endpoints
├── verus_node_rpc.py             # Existing RPC methods (register_name_commitment, etc.)
├── verusid_client.py             # NEW: VerusIdInterface wrapper for provisioning
├── clients/typescript/src/client.ts  # Existing TypeScript client (extend with provisioning)
└── tests/
    ├── test_provisioning_api.py  # NEW
    └── test_provisioning_worker.py  # NEW
```

The provisioning module is **strictly additive** — no existing registration code is modified.

---

## Phase Plan

### Phase 1: Primitives — ProvisioningChallenge Builder

**Goal:** Build a `ProvisioningChallenge` using `verus-typescript-primitives`.

Tasks:

1. Install `verus-typescript-primitives` in the service (already available via
   `verusid-ts-client` transitive, but provision-specific classes may need direct import).
   Confirm all provisioning classes are re-exported from the package index.

2. Create `provisioning/challenge.py`:

   ```python
   # provisioning/challenge.py

   import os
   import time
   import random
   from typing import Optional
   from verus_typescript_primitives import (
       ProvisioningChallenge,
       CompactIAddressObject,
       CompactAddressObject,
       RequestURI,
   )

   SIGNING_IDENTITY = os.getenv("SIGNING_IDENTITY")       # i-address of service identity
   SIGNING_WIF = os.getenv("PROVISIONING_SIGNING_WIF")   # WIF for signing challenges
   I_ADDRESS_VERSION = int(os.getenv("I_ADDRESS_VERSION", "0"))
   DEFAULT_SYSTEM_ID = os.getenv("DEFAULT_SYSTEM_ID", "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV")

   def _check_required():
       if not SIGNING_IDENTITY or not SIGNING_WIF:
           raise RuntimeError("SIGNING_IDENTITY and PROVISIONING_SIGNING_WIF must be set")

   def build_provisioning_challenge(
       name: str,
       parent: str,
       primary_raddress: str,
       callback_url: Optional[str] = None,
       system_id: Optional[str] = None,
   ) -> tuple[ProvisioningChallenge, str]:
       """
       Build and sign a ProvisioningChallenge.

       Returns (challenge, challenge_id) where challenge_id is the deeplink fragment.
       The challenge is signed with SIGNING_WIF.
       """
       _check_required()

       challenge_id = to_base58_check(
           random.randbytes(20), I_ADDRESS_VERSION
       )
       system_i_addr = system_id or DEFAULT_SYSTEM_ID

       # Build the challenge object
       challenge = ProvisioningChallenge({
           challenge_id: challenge_id,
           name: name,
           parent: _parent_to_iaddress(parent),
           system_id: system_i_addr,
           created_at: int(time.time()),
           salt: to_base58_check(random.randbytes(16), I_ADDRESS_VERSION),
           context: None,
       })

       # Sign the challenge
       signed = sign_challenge(challenge, SIGNING_WIF, SIGNING_IDENTITY)
       return signed, challenge_id

   def _parent_to_iaddress(parent: str) -> str:
       """Convert a parent namespace (e.g. 'bitcoins.vrsc') to an i-address if needed."""
       # If already an i-address, return as-is
       if parent.startswith("i"):
           return parent
       # Otherwise resolve via getidentity or return parent as-is for now
       return parent  # TODO: resolve friendly name to i-address

   def to_base58_check(data: bytes, version: int) -> str:
       # existing utility from primitives
       ...

   def sign_challenge(challenge: ProvisioningChallenge, wif: str, identity: str):
       # Uses VerusIdInterface.createLoginConsentRequest pattern
       # or direct ECDSA sign with Verus signing utilities
       ...
   ```

3. Add to `SFConstants.py` / env:

   - `SIGNING_IDENTITY` — service i-address (e.g. `i5w5...`)
   - `PROVISIONING_SIGNING_WIF` — WIF private key for signing challenges
   - `DEFAULT_SYSTEM_ID` — system i-address (e.g. `i5w5...` for VRSC mainnet)
   - `PROVISIONING_CALLBACK_BASE_URL` — base URL for wallet callback

TDD acceptance:

- `test_provisioning_challenge_build` — given name + parent, produces a
  `ProvisioningChallenge` with correct `name`, `parent`, `system_id`, `challenge_id`
- `test_provisioning_challenge_signed` — challenge must be signed (signature field non-null)
- `test_provisioning_challenge_requires_env` — missing env vars raises RuntimeError

---

### Phase 2: Verification — ProvisioningRequest Signature Check

**Goal:** Verify the wallet's signature on a `ProvisioningRequest`.

Tasks:

1. Create `provisioning/verification.py`:

   The `ProvisioningRequest` contains:
   - `challenge` — the original challenge (name, parent, system_id, challenge_id, created_at)
   - `signing_address` — the wallet's R-address that approved
   - `signature` — ECDSA signature over the challenge hash

   Verification steps:
   1. Reconstruct the challenge hash from `request.challenge`
   2. Recover the signer's address from `request.signature`
   3. Confirm recovered address matches `request.signing_address`
   4. Confirm `challenge.challenge_id` matches what we issued
   5. Confirm `challenge.created_at` is not expired (max age: e.g. 10 minutes)
   6. Confirm `challenge.name` matches what was requested

2. Add `verusid_client.py` — thin wrapper around `VerusIdInterface`:

   ```python
   # verusid_client.py

   from verusid_ts_client import VerusIdInterface
   import os

   VERUS_SYSTEM_ID = os.getenv("VERUS_SYSTEM_ID", "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV")
   VERUS_RPC_URL = os.getenv("VERUS_RPC_URL", "http://localhost:27486")

   _client = None

   def get_verusid_client():
       global _client
       if _client is None:
           _client = VerusIdInterface(VERUS_SYSTEM_ID, VERUS_RPC_URL)
       return _client

   def verify_provisioning_request(provisioning_request) -> bool:
       """
       Verify the signature on a ProvisioningRequest.
       Delegates to VerusIdInterface.verifyLoginConsentResponse or a dedicated
       provisioning verification method if available.
       """
       client = get_verusid_client()
       return client.verifyProvisioningRequest(provisioning_request)
   ```

   **Note:** If `VerusIdInterface` does not yet have a
   `verifyProvisioningRequest` method, this phase defers to Phase 5 (MCP/primitives
   update) and uses a manual ECDSA verification shim in the interim.

TDD acceptance:

- `test_verify_valid_request` — valid signature passes
- `test_verify_tampered_request` — tampered challenge fails
- `test_verify_expired_request` — challenge older than TTL fails
- `test_verify_wrong_challenge_id` — replayed challenge_id fails
- `test_verify_mismatched_signer` — signing_address != recovered signer fails

---

### Phase 3: Response Builder — ProvisioningResponse

**Goal:** Construct the `ProvisioningResponse` returned to the wallet.

Tasks:

1. Create `provisioning/response.py`:

   ```python
   # provisioning/response.py

   from verus_typescript_primitives import (
       ProvisioningResponse,
       ProvisioningDecision,
       ProvisioningResult,
       LOGIN_CONSENT_PROVISIONING_RESULT_STATE_COMPLETE,
       LOGIN_CONSENT_PROVISIONING_RESULT_STATE_FAILED,
   )

   def build_provisioning_response(
       challenge,          # ProvisioningChallenge we issued
       signing_address,    # wallet's R-address that signed
       result: ProvisioningResult,
   ) -> ProvisioningResponse:
       """
       Build a ProvisioningResponse wrapping a ProvisioningDecision
       that embeds the result.
       """
       decision = ProvisioningDecision({
           decision_id: challenge.challenge_id,  # same ID for correlation
           created_at: int(time.time()),
           request: provisioning_request,        # the request that was verified
           result: result,
       })
       return ProvisioningResponse({
           system_id: challenge.system_id,
           signing_id: SIGNING_IDENTITY,
           decision: decision,
       })
   ```

2. `ProvisioningResult` states (from VDXF keys):
   - `LOGIN_CONSENT_PROVISIONING_RESULT_STATE_COMPLETE`
   - `LOGIN_CONSENT_PROVISIONING_RESULT_STATE_FAILED`
   - `LOGIN_CONSENT_PROVISIONING_RESULT_STATE_PENDINGREQUIREDINFO`
   - `LOGIN_CONSENT_PROVISIONING_RESULT_STATE_PENDINGAPPROVAL`

3. On success, populate `ProvisioningResult` fields:
   - `identity_address` — the new i-address
   - `fully_qualified_name` — e.g. `alice.VRSC@`
   - `provisioning_txids` — txid of the `registeridentity` call
   - `state` = COMPLETE

4. On failure, populate:
   - `state` = FAILED
   - `error_key` — one of: `nametaken`, `unknown`, `commitment`, `creation`, `transfer`
   - `error_desc` — human-readable message

TDD acceptance:

- `test_build_success_response` — correct fields populated
- `test_build_failure_response` — error_key + error_desc set
- `test_response_serialization_roundtrip` — toBuffer/fromBuffer produces same bytes

---

### Phase 4: API Endpoints

**Goal:** Add FastAPI routes for the full provisioning ceremony.

Endpoints:

#### `POST /api/provisioning/challenge`

Create and sign a provisioning challenge. Returns a challenge object (or deeplink URI).

```python
class ProvisioningChallengeRequest(BaseModel):
    name: str = Field(..., examples=["alice"])
    parent: str = Field(..., examples=["VRSC"])           # parent namespace
    primary_raddress: str = Field(..., examples=["Ralice..."])  # user's R-address
    system_id: str | None = Field(default=None, examples=["i5w5..."])
    callback_url: str | None = Field(default=None)       # wallet POST target
```

Response (200):

```json
{
  "challenge_id": "...",
  "challenge": { /* ProvisioningChallenge fields */ },
  "deeplink_uri": "verus://provision?challenge=<base64>",
  "expires_at": 1719999999
}
```

#### `POST /api/provisioning/request`

Receive the wallet-signed `ProvisioningRequest`, verify it, execute registration,
return `ProvisioningResponse`.

```python
class ProvisioningRequestPayload(BaseModel):
    provisioning_request: dict  # parsed ProvisioningRequest JSON
```

Response (200):

```json
{
  "provisioning_response": { /* ProvisioningResponse fields */ },
  "registration_request_id": "uuid"  # links to existing /api/status
}
```

Error responses:

- `400` — invalid/mismatched challenge_id, expired, signature invalid
- `409` — name already taken (on-chain check failed)
- `503` — RPC error during registration

#### `GET /api/provisioning/status/{challenge_id}`

Poll provisioning status by challenge_id (maps to `decision_id`).

#### `POST /api/provisioning/webhook` (optional)

Receive callbacks from the wallet/identity service (if wallet POSTs instead of
returning inline).

---

Tasks:

1. Add to `provisioning/router.py`:

   ```python
   from fastapi import APIRouter, HTTPException, Security
   from pydantic import BaseModel, Field
   from .challenge import build_provisioning_challenge
   from .verification import verify_provisioning_request
   from .response import build_provisioning_response, build_failure_result

   router = APIRouter(prefix="/api/provisioning", tags=["provisioning"])

   CHALLENGE_MAX_AGE_SECONDS = int(os.getenv("PROVISIONING_CHALLENGE_MAX_AGE_SECONDS", "600"))

   # In-memory store for issued challenges (challenge_id -> challenge object)
   # Production: move to Redis/DB
   _challenge_store: dict[str, tuple[ProvisioningChallenge, float]] = {}

   @router.post("/challenge")
   def create_provisioning_challenge(request: ProvisioningChallengeRequest):
       challenge, challenge_id = build_provisioning_challenge(
           name=request.name,
           parent=request.parent,
           primary_raddress=request.primary_raddress,
           callback_url=request.callback_url,
           system_id=request.system_id,
       )
       _challenge_store[challenge_id] = (challenge, time.time())
       deeplink = f"verus://provision?challenge={challenge.toBuffer().toString('base64')}"
       return {
           "challenge_id": challenge_id,
           "challenge": challenge.toJson(),
           "deeplink_uri": deeplink,
           "expires_at": int(time.time()) + CHALLENGE_MAX_AGE_SECONDS,
       }

   @router.post("/request")
   def submit_provisioning_request(payload: ProvisioningRequestPayload):
       from verus_typescript_primitives import ProvisioningRequest as TSProvisioningRequest

       req = TSProvisioningRequest.fromJson(payload.provisioning_request)
       challenge_id = req.challenge.challenge_id

       # 1. Retrieve and validate challenge
       if challenge_id not in _challenge_store:
           raise HTTPException(400, "Unknown challenge_id")
       stored_challenge, issued_at = _challenge_store[challenge_id]
       if time.time() - issued_at > CHALLENGE_MAX_AGE_SECONDS:
           del _challenge_store[challenge_id]
           raise HTTPException(400, "Challenge expired")

       # 2. Verify signature
       if not verify_provisioning_request(req):
           raise HTTPException(400, "Invalid signature")

       # 3. Execute on-chain registration via existing RPC (reuse worker logic)
       registration_result = _execute_provisioning_registration(req)

       # 4. Build and return provisioning response
       response = build_provisioning_response(
           challenge=stored_challenge,
           signing_address=req.signing_address,
           result=registration_result,
       )
       return {
           "provisioning_response": response.toJson(),
           "registration_request_id": registration_result.request_id,
       }
   ```

2. Mount the router in `id_create_service.py`:

   ```python
   from provisioning.router import router as provisioning_router
   app.include_router(provisioning_router)
   ```

3. Integrate with existing worker for the on-chain step:

   The `submit_provisioning_request` handler calls the existing
   `register_name_commitment` + `register_identity` flow (via `worker.py` or
   directly through `verus_node_rpc.py`). The provisioning-specific bit is
   the challenge/request ceremony wrapping the same RPC calls.

   Alternatively, create a `provisioning_registrations` table (parallel to
   `registrations`) to track provisioning jobs through the worker, then
   expose via `/api/provisioning/status/{challenge_id}`.

TDD acceptance:

- `test_challenge_endpoint_requires_api_key`
- `test_challenge_endpoint_returns_deeplink`
- `test_request_endpoint_rejects_unsigned`
- `test_request_endpoint_rejects_expired_challenge`
- `test_request_endpoint_rejects_unknown_challenge_id`
- `test_request_endpoint_returns_provisioning_response`
- `test_status_endpoint_returns_current_state`
- `test_status_returns_404_for_unknown_challenge`

---

### Phase 5: Worker Integration (Provisioning-Specific Jobs)

**Goal:** Handle async confirmation polling for provisioning transactions.

Tasks:

1. Add a `provisioning_registrations` table (or extend existing `registrations`
   with a `type` column — `"commitment"` vs `"provisioning"`):

   ```sql
   CREATE TABLE provisioning_registrations (
       id TEXT PRIMARY KEY,
       challenge_id TEXT NOT NULL UNIQUE,
       name TEXT NOT NULL,
       parent TEXT NOT NULL,
       primary_raddress TEXT NOT NULL,
       system_id TEXT NOT NULL,
       signing_address TEXT NOT NULL,
       status TEXT NOT NULL,          -- pending_rnc_confirm | ready_for_idr | idr_submitted | complete | failed
       rnc_txid TEXT,
       idr_txid TEXT,
       provisioned_identity_address TEXT,
       fully_qualified_name TEXT,
       error_message TEXT,
       attempts INTEGER NOT NULL DEFAULT 0,
       next_retry_at TIMESTAMP,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   );
   ```

2. Extend `worker.py` with a `process_provisioning_once()` function that:

   - Polls `pending_rnc_confirm` provisioning rows → confirms → transitions to `ready_for_idr`
   - Polls `ready_for_idr` provisioning rows → calls `registeridentity` → `idr_submitted`
   - Polls `idr_submitted` provisioning rows → confirms → `complete`
   - Fires webhook on terminal state

3. Add a `GET /api/provisioning/status/{challenge_id}` endpoint that reads
   from `provisioning_registrations`.

TDD acceptance:

- Worker advances provisioning row through all states
- `complete` row has `provisioned_identity_address` and `fully_qualified_name`
- Failed row has `error_message` and `error_key`
- Webhook fires on terminal state

---

### Phase 6: TypeScript Client Updates

**Goal:** Add provisioning methods to `clients/typescript/src/client.ts`.

Tasks:

1. Extend `IdCreateClient`:

   ```typescript
   // In clients/typescript/src/client.ts

   export type ProvisioningChallengeRequest = {
     name: string;
     parent: string;
     primary_raddress: string;
     system_id?: string;
     callback_url?: string;
   };

   export type ProvisioningChallengeResponse = {
     challenge_id: string;
     challenge: ProvisioningChallengeJson;
     deeplink_uri: string;
     expires_at: number;
   };

   export type ProvisioningRequestPayload = {
     provisioning_request: ProvisioningRequestJson;
   };

   export type ProvisioningResponseResult = {
     provisioning_response: ProvisioningResponseJson;
     registration_request_id: string;
   };

   export class IdCreateClient {
     // ... existing methods ...

     async createProvisioningChallenge(
       input: ProvisioningChallengeRequest
     ): Promise<ProvisioningChallengeResponse> {
       return this.request<ProvisioningChallengeResponse>(
         "POST",
         "/api/provisioning/challenge",
         input
       );
     }

     async submitProvisioningRequest(
       request: ProvisioningRequestJson
     ): Promise<ProvisioningResponseResult> {
       return this.request<ProvisioningResponseResult>(
         "POST",
         "/api/provisioning/request",
         { provisioning_request: request }
       );
     }

     async getProvisioningStatus(
       challengeId: string
     ): Promise<ProvisioningStatusResponse> {
       return this.request<ProvisioningStatusResponse>(
         "GET",
         `/api/provisioning/status/${encodeURIComponent(challengeId)}`
       );
     }
   }
   ```

2. Add a `ProvisionIdentityDetails` wrapper if helpful for clients constructing
   provisioning info (the reverse direction — service → wallet).

TDD acceptance: Add `clients/typescript/examples/provisioningExample.ts` demonstrating:

1. Call `createProvisioningChallenge`
2. Encode challenge into deeplink / hand off to wallet
3. Poll `getProvisioningStatus` OR receive webhook

---

### Phase 7: Linkage — Provisioning in Login Flow (Optional Enhancement)

**Goal:** Allow `svc-idlogin` to embed `provisioning_info` in login challenges
for users who don't yet have an identity.

This is purely additive and optional. It does **not** change provisioning
endpoints.

Tasks:

1. In `svc-idlogin` `src/index.js`, when building the `LoginConsentChallenge`,
   check if the user has an identity. If not, include `provisioning_info`:

   ```javascript
   // In /api/login/start
   const provisioningHint = new primitives.ProvisioningInfo(
     `${BASE_URL}/api/provisioning/challenge?name=${encodeURIComponent(preferredName)}&parent=${encodeURIComponent(parent)}`,
     primitives.PROVISION_IDENTITY_DETAILS_VDXF_KEY.vdxfid
   );

   const challenge = new LoginConsentChallenge({
     // ...
     provisioning_info: [provisioningHint],
   });
   ```

   The user sees "You don't have an identity yet — create one?" inline in the
   wallet's login approval screen, and can navigate directly to provisioning.

2. The deeplink returned by `/api/provisioning/challenge` can then be passed back
   to the wallet to complete provisioning without a second QR scan.

TDD acceptance: Login challenge includes `provisioning_info` when user has no identity.

---

## Environment Variables

New env vars to add to `env.sample`:

```bash
# Service identity used to sign provisioning challenges
SIGNING_IDENTITY="i..."
PROVISIONING_SIGNING_WIF="..."

# System i-address for provisioning (usually same as DEFAULT_SYSTEM_ID)
DEFAULT_SYSTEM_ID="i..."

# Callback URL the wallet POSTs to (if using async webhook model)
PROVISIONING_CALLBACK_BASE_URL="https://your-service.com"

# Max age of an unsigned challenge before it is rejected (seconds)
PROVISIONING_CHALLENGE_MAX_AGE_SECONDS=600

# Verus RPC for signature verification (may differ from registration RPC)
VERUS_RPC_URL="http://user:pass@host:port"
```

---

## New Test Files

| File | Coverage |
|---|---|
| `tests/test_provisioning_api.py` | API endpoints (challenge, request, status) |
| `tests/test_provisioning_challenge.py` | Challenge building and signing |
| `tests/test_provisioning_verification.py` | Signature verification |
| `tests/test_provisioning_response.py` | Response building + roundtrip |
| `tests/test_provisioning_worker.py` | Worker state transitions |
| `tests/test_provisioning_client.py` | TypeScript client methods |

---

## Dependency Requirements

```bash
# Service Python dependencies
verus-typescript-primitives  # for ProvisioningChallenge, ProvisioningRequest, etc.
verusid-ts-client            # for VerusIdInterface (signature verification)
slickrpc                     # already present, for registeridentity RPC

# TypeScript client dev dependencies
tsx                          # already present
@types/node                  # already present
```

Verify provisioning classes are importable from the primitives package:

```bash
cd /home/mylo/verus-typescript-primitives
npm run build
node -e "const p = require('.'); console.log(Object.keys(p).filter(k => k.includes('Provisioning')))"
```

---

## Open Questions / Deferred Decisions

1. **Challenge store**: For now use an in-memory `dict` (Phase 4). For production,
   consider Redis with TTL or a `provisioning_challenges` DB table. Decide before
   Phase 7 (deployment).

2. **`VerusIdInterface` verification method**: If
   `VerusIdInterface.verifyProvisioningRequest` does not exist, implement a
   manual ECDSA verify shim using the Verus signing utilities. File issue on
   `verusid-ts-client` to add the method.

3. **Parent name resolution**: `_parent_to_iaddress` currently returns the input
   if it doesn't start with `i`. A production implementation should call
   `getidentity` to resolve a friendly name (e.g. `VRSC`) to its i-address before
   constructing the challenge.

4. **Webhook vs inline response**: The plan above uses inline response (Phase 4).
   If wallet POSTs the `ProvisioningRequest` asynchronously, a separate
   `/api/provisioning/webhook` endpoint and async result storage is needed.
   Evaluate based on wallet integration requirements.

---

## Definition of Done

- [ ] `POST /api/provisioning/challenge` creates and signs a `ProvisioningChallenge`
- [ ] `POST /api/provisioning/request` verifies wallet signature, executes on-chain
      registration, returns `ProvisioningResponse`
- [ ] `GET /api/provisioning/status/{challenge_id}` returns current state
- [ ] Worker processes provisioning rows through all states (`pending_rnc_confirm`
      → `complete` or `failed`)
- [ ] Webhook fires on terminal provisioning state
- [ ] TypeScript client has `createProvisioningChallenge`,
      `submitProvisioningRequest`, `getProvisioningStatus` methods
- [ ] All new tests pass: `uv run pytest tests/test_provisioning_*.py -q`
- [ ] No regression in existing registration flow: `uv run pytest tests/ -q`
