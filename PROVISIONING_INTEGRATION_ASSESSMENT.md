# Provisioning Integration Assessment

## Status

Assessment as of 2026-06-30. This document supersedes and replaces the
in-repo provisioning docs (`PROVISIONING_GUIDE.md`,
`PROVISIONING_REFACTOR_PLAN.md`, `PROVISIONING_IMPLEMENTATION_PLAN.md`,
`provisioning_update.md`), which described a non-working in-repo
provisioning implementation and have been removed.

## Context

The in-repo provisioning implementation under `provisioning/` (Python
adapters/engine/router + Node `src/` scripts + a `svc-provisioning`
container in `docker-compose.yml`) does not work. A working alternative
exists as an external, self-contained service:

- https://github.com/Meyse/local-verusid-provisioning-webhook

This assessment evaluates (a) whether the in-repo provisioning code can
be removed, and (b) how to integrate the friend's working service with
`svc-idcreate`.

## Part 1 — In-repo provisioning code is safe to remove

### Why removal is safe

**Fully superseded by the external repo.** The friend's service is a
self-contained TypeScript app (own `package.json`, `yarn.lock`, `src/`,
`views/`, `data/` store) that implements the entire provisioning flow
end-to-end: signed QR generation, the wallet webhook, signature
verification, `registernamecommitment` + `registeridentity` RPCs,
confirmation polling, and signed `COMPLETE` responses. It talks to a
local `verusd` directly and does not depend on `svc-idcreate`, so it
replaces **both** the in-repo `provisioning/` module **and** the
separate `svc-provisioning` container.

**Cleanly decoupled from the core service.** The docs themselves
describe provisioning as "strictly additive," and the code confirms it:

- `worker.py` has zero provisioning references — it only handles the
  core name-commitment → `registeridentity` flow.
- The provisioning engine uses its own `provisioning_challenges` SQLite
  table, separate from the core `registrations` table (no shared
  schema/migration to unwind).
- The only wiring into the app is the import and `include_router` call
  in `id_create_service.py`. The core `POST /api/register` flow is
  untouched.

### What was removed

| Category | Items |
|---|---|
| Directory | `provisioning/` (Python adapters/engine/router + Node `src/`, `package.json`, `node_modules/`) |
| App wiring | Import + `app.include_router(provisioning_router)` in `id_create_service.py` |
| Compose | `provisioning` service block + `api`'s `depends_on: provisioning` in `docker-compose.yml` |
| Tests | `tests/test_provisioning_{api,adapter_selection,golden_vectors,http_adapter_contract}.py` + `tests/fixtures/provisioning_golden_vectors.json` |
| Scripts | `scripts/provisioning_phase6_{staging,canary,full_cutover}_check.sh`, `scripts/run_provisioning_http_tests.sh` |
| Docs (deleted) | `PROVISIONING_GUIDE.md`, `PROVISIONING_REFACTOR_PLAN.md`, `PROVISIONING_IMPLEMENTATION_PLAN.md`, `provisioning_update.md` |
| Docs (edited) | `README.md`, `DEPLOYMENT.md` (§8 + provisioning service mention), `env.sample` (provisioning env block), `AGENTS.md` (provisioning mentions) |

Out of scope (left untouched — separate concepts, not the in-repo
provisioning implementation):

- `companion_app_design.md` (`/v1/sign/provision` is the companion
  app's signing route, unrelated).
- `ID_AVAILABILITY_WORKFLOW_PLAN.md` (aspirational mention only).

## Part 2 — Co-existence and handoff to the friend's service

### What each service owns

| | svc-idcreate | friend's service |
|---|---|---|
| Scope | non-interactive `POST /api/register` + worker + webhooks | full interactive slice: QR → wallet sig → `registernamecommitment` → confirm → `registeridentity` → signed `COMPLETE` |
| State | Postgres/SQLite `registrations` table | file store `data/provisioning-requests.json` |
| RPC | svc-idcreate's `verusd` | his local `verusd` (VRSCTEST conf) |
| Port | 5003 (+ worker) | 3010 |
| Status API | `GET /api/status/{id}` | `GET /provision/status/:challengeId` |

No shared DB, no shared state, no port collision — they're independent
processes. His service exposes clean HTTP endpoints:

- `POST /api/generate-provisioning-qr` → returns `deeplink`, `qrDataUrl`, `requestId`
- `POST /provision` → wallet webhook (wallet posts the signed request)
- `GET /provision/status/:challengeId` → poll status
- `GET /api/parents`, `GET /api/signers` → enumerate available parents/signers

### Three viable handoff models (least → most invasive)

**Model A — Side-by-side (no integration).** Client calls svc-idcreate
for non-interactive registration and the friend's service for wallet
provisioning. Zero work, but two APIs / status endpoints / stores.

**Model B — svc-idcreate proxies (recommended).** Add a thin
`/api/provisioning/*` set of endpoints to svc-idcreate that forward to
the friend's service (`generate-provisioning-qr` → return deeplink/QR;
`status/{id}` → his `/provision/status/:challengeId`). Client keeps one
API surface. His service still owns the on-chain RPC and the 15s
automation loop. This is ~30 lines of `httpx` calls in a new router.

**Model C — completion callback into svc-idcreate.** His service does
the work, and on `COMPLETE` POSTs the final identity (fqn + i-address +
txid) to svc-idcreate's existing registration webhook so a
`registrations` row is created for bookkeeping. Caveat: his
`automation.ts` currently has no outbound callback — it only writes to
its file store and serves poll status. This needs a small change on his
side (a `fetch()` to the webhook when `state === "complete"`).

### Hard constraints to be aware of

His service is explicitly a **local dev tool**
(`config.example.js` binds `127.0.0.1`, reads a macOS VRSCTEST conf
path, file-based store, README says "not a hosted service"). For
co-existence beyond a single dev box:

- run it as a sidecar on the same host as svc-idcreate and point
  svc-idcreate at `http://127.0.0.1:3010` (Model B), or
- have him set `UI_HOST=0.0.0.0` on a trusted LAN + front it with a
  tunnel/auth.

Also: his automation loop calls `registeridentity` itself, so
**svc-idcreate's worker will never see provisioned IDs** unless Model C
is added. Status for provisioned identities must come from his
`/provision/status/:challengeId`.

## Recommendation

Go with **Model B now** (proxy), and if unified record-keeping is
wanted later, ask the friend to add a single `COMPLETE` webhook to
`automation.ts` (Model C on top). This keeps the existing
`POST /api/register` + worker untouched, removes all the dead in-repo
`provisioning/` code, and integrates the working flow behind one API
with minimal coupling.

## Implementation Plan (Model B — to be done in a follow-up)

1. Add `PROVISIONING_WEBHOOK_BASE_URL` env var (default
   `http://127.0.0.1:3010`) to `env.sample`.
2. Create a new thin `provisioning_proxy.py` router in svc-idcreate
   that forwards to the friend's service using `httpx`:
   - `POST /api/provisioning/qr` → his `POST /api/generate-provisioning-qr`
   - `GET /api/provisioning/status/{challenge_id}` → his `GET /provision/status/:challengeId`
   - optionally: `GET /api/provisioning/parents`, `GET /api/provisioning/signers`
3. Mount the proxy router in `id_create_service.py` behind the
   existing `X-API-Key` security.
4. Add tests (`tests/test_provisioning_proxy.py`) using a mocked
   `httpx` transport, covering success + downstream-error mapping.
5. Document the proxy routes + the `PROVISIONING_WEBHOOK_BASE_URL`
   dependency in `README.md` and `DEPLOYMENT.md`.

Model C (optional, later) requires a coordinated change on the
friend's side and is out of scope for the initial integration.
