# Provisioning Guide (Routes, Workflow, Deployment)

## Status

This is the canonical provisioning reference for `svc-idcreate` as of 2026-04-02.

Supersedes:
- `PROVISIONING_REFACTOR_PLAN.md` (historical)
- `PROVISIONING_IMPLEMENTATION_PLAN.md` (historical)
- provisioning-specific sections previously embedded in `DEPLOYMENT.md`

## Components

- `svc-idcreate` (FastAPI): external API used by clients.
- `svc-provisioning` (Node HTTP service): provisioning primitive operations.
- shared SQLite DB (`REGISTRAR_DB_PATH`) for registration/provisioning state.

## Environment

Required provisioning env in `svc-idcreate`:

```env
PROVISIONING_ADAPTER_MODE=http
PROVISIONING_SERVICE_URL=http://svc-provisioning:5055
PROVISIONING_HTTP_TIMEOUT_SECONDS=10
PROVISIONING_RETRY_COUNT=1
PROVISIONING_LOG_LEVEL=INFO
```

## External API Routes (`svc-idcreate`)

Base prefix: `/api/provisioning`

Authentication:
- `POST /challenge` and `POST /request` require `X-API-Key`.
- `GET /status/{challenge_id}` is currently public.

### POST `/api/provisioning/challenge`

Purpose:
- Create and persist a provisioning challenge.

Request body:

```json
{
  "name": "alice",
  "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
  "primary_raddress": "RExampleAddress123",
  "system_id": "i7LaXD2cdy1ze33eHzZaEPyueT4yQmBfW"
}
```

Response highlights:
- `challenge_id`
- `deeplink_uri`
- `challenge_json`
- `challenge_hex`
- `expires_at`

### POST `/api/provisioning/request`

Purpose:
- Accept wallet-signed provisioning request.
- Verify request against stored challenge.
- Queue on-chain registration and return a provisioning response envelope.

Request body:

```json
{
  "provisioning_request": { "...": "wallet payload" }
}
```

Response highlights:
- `provisioning_response` (serialized decision payload)
- `request_id` (registration tracker ID)

Error behavior:
- `400` for invalid request or verification failures.
- `409` when challenge is already consumed (replay protection).
- `503` when service config/RPC dependencies are unavailable.

### GET `/api/provisioning/status/{challenge_id}`

Purpose:
- Return current provisioning state by challenge ID.

Response highlights:
- `status` (`pending`, `request_received`, `submitted`, `failed`, `complete`)
- `request_id`
- `identity_address`
- `fully_qualified_name`
- failure context (`error_key`, `error_message`)

## Internal Primitive Routes (`svc-provisioning`)

These are internal service routes used by `svc-idcreate` through `HttpProvisioningAdapter`.

- `GET /health`
- `POST /v1/provisioning/challenge/build`
- `POST /v1/provisioning/request/verify`
- `POST /v1/provisioning/response/build`
- `POST /v1/base58check/encode`

All internal routes return JSON.

## Workflow

1. Client calls `POST /api/provisioning/challenge` with target identity info.
2. `svc-idcreate` asks `svc-provisioning` to build challenge primitives.
3. `svc-idcreate` stores challenge record and returns deeplink artifacts.
4. Wallet signs challenge and client submits `POST /api/provisioning/request`.
5. `svc-idcreate` verifies request through `svc-provisioning`.
6. `svc-idcreate` queues registration in DB and returns a provisioning response envelope.
7. Worker processes registration lifecycle and updates terminal status.
8. Client polls `GET /api/provisioning/status/{challenge_id}` until complete/failed.

## Deployment Guide (Provisioning)

### Local or Compose startup

From `svc-idcreate`:

```bash
docker compose -f docker-compose.yaml up -d svc-provisioning idcreate-api idcreate-worker
```

Health checks:

```bash
curl -s http://localhost:5055/health
curl -s "http://localhost:5003/health?native_coin=VRSC"
```

### Recommended validation sequence

```bash
./scripts/provisioning_phase6_staging_check.sh
./scripts/provisioning_phase6_canary_check.sh
./scripts/provisioning_phase6_full_cutover.sh
```

Or run the default HTTP provisioning suite directly:

```bash
./scripts/run_provisioning_http_tests.sh
```

### Logging and troubleshooting

- `svc-idcreate` provisioning logs are controlled by `PROVISIONING_LOG_LEVEL`.
- `svc-provisioning` emits structured JSON logs per request.
- Common checks:
  - confirm `PROVISIONING_SERVICE_URL` resolves from `idcreate-api` container.
  - confirm `/health` on `svc-provisioning`.
  - verify API key presence for protected routes.

### Rollback policy

- Roll back container image/tag if a release is unstable.
- Keep provisioning mode HTTP-only (`PROVISIONING_ADAPTER_MODE=http`).
- Restore a known-good `svc-provisioning` image endpoint and rerun validation scripts.
