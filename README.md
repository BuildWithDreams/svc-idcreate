# identity-creation-service

Deployment instructions: see `DEPLOYMENT.md`.
Provisioning routes/workflow/deployment guide: see `PROVISIONING_GUIDE.md`.
Thin API clients: `clients/python` and `clients/typescript`.
Verus contentmultimap storage implementation: `CONTENTMULTIMAP_STORAGE_GUIDE.md`.
Phased TDD storage plan: `STORAGE_IMPLEMENTATION_GUIDE.md`.

## Documentation Index

Active docs:
- `README.md` (service overview and local usage)
- `DEPLOYMENT.md` (runtime deployment and operations)
- `PROVISIONING_GUIDE.md` (provisioning routes, workflow, and deployment)
- `MCP_GUIDE.md` (MCP integration and usage)
- `mcp_server/python/README.md` (MCP server package details)

Redundant or historical docs:
- `PROVISIONING_REFACTOR_PLAN.md` (historical migration plan; superseded by `PROVISIONING_GUIDE.md`)
- `PROVISIONING_IMPLEMENTATION_PLAN.md` (historical implementation design; superseded by `PROVISIONING_GUIDE.md`)

### MCP server (Python)

MCP tools currently implemented:

- `create_identity`
- `get_identity_request_status`
- `wait_for_identity_completion`
- `list_recent_identity_failures`
- `requeue_identity_webhook`

Run MCP server from repo root:

```bash
uv sync
IDCREATE_BASE_URL="http://localhost:5003" \
IDCREATE_API_KEY="key1" \
IDCREATE_TIMEOUT_SECONDS="15" \
uv run python mcp_server/python/server.py
```

MCP-focused tests:

```bash
uv run pytest tests/test_mcp_tools.py tests/test_mcp_server_smoke.py -q
```

Provisioning HTTP integration tests (default path):

```bash
./scripts/run_provisioning_http_tests.sh
```

This command:

- starts `svc-provisioning` from `docker-compose.yaml`
- runs provisioning tests with `PROVISIONING_ADAPTER_MODE=http`
- uses `PROVISIONING_SERVICE_URL=http://127.0.0.1:5055`

Operational tips:

- Keep `IDCREATE_API_KEY` only in MCP runtime environment.
- Use bounded polling values with `wait_for_identity_completion` (reasonable `timeout_seconds` and `poll_seconds`).
- Triage failed requests with `list_recent_identity_failures` before retrying webhook delivery.
- Requeue webhook only for terminal requests using `requeue_identity_webhook`.
- If you see `ModuleNotFoundError: No module named 'mcp'`, run `uv sync`.

For full MCP details, see `MCP_GUIDE.md` and `mcp_server/python/README.md`.

```bash
uv init --app
uv add fastapi --extra standard
uv run fastapi dev_or_run file.py --port 5003

uv add --dev pytest
```

### Run locally

```bash
uv sync
uv run fastapi dev id_create_service.py --port 5003
```

Swagger/OpenAPI:

- `http://localhost:5003/docs`
- `http://localhost:5003/openapi.json`

### Common `uv` commands for dependencies:

* **Add a package:** `uv add <package_name>`
* **Add multiple:** `uv add qrcode pillow`
* **Add a dev dependency:** `uv add --dev pytest`
* **Remove a package:** `uv remove <package_name>`
* **Sync environment:** `uv sync` (Run this if you manually edit `pyproject.toml`)

### RPC daemon enablement

RPC connections are now controlled by environment flags per daemon.

- Set `<daemon>_rpc_enabled=true` to enable a daemon.
- If a daemon is disabled (or the flag is missing), it is not loaded into `DAEMON_CONFIGS`.
- If a daemon is enabled, all corresponding RPC vars must be set: `<daemon>_rpc_user`, `<daemon>_rpc_password`, `<daemon>_rpc_port`, and `<daemon>_rpc_host`.

Example for VRSC:

```env
verusd_vrsc_rpc_enabled=true
verusd_vrsc_rpc_user=...
verusd_vrsc_rpc_password=...
verusd_vrsc_rpc_port=...
verusd_vrsc_rpc_host=...
```

### Health endpoint behavior

The `/health` endpoint checks RPC connectivity by calling `getinfo`.

- Configure daemon name with `HEALTH_RPC_DAEMON` (default: `verusd_vrsc`).
- Pass `native_coin` query parameter (for example `VRSC`, `VARRR`, `VDEX`, `CHIPS`) to select daemon by native ticker.
- The ticker-based check only considers enabled daemons from `DAEMON_CONFIGS`.
- If RPC is reachable, `/health` returns `200` with RPC `info`.
- If no daemon matches the requested ticker, or RPC is disabled/unconfigured/unreachable, `/health` returns `503`.

Examples:

```text
GET /health
GET /health?native_coin=VRSC
```

### Registration API

This service follows an async workflow:

1. `POST /api/register` broadcasts name commitment and stores request state.
2. Worker advances state in background.
3. `GET /api/status/{request_id}` returns current lifecycle status.

Current lifecycle statuses:

- `pending_rnc_confirm`
- `ready_for_idr`
- `idr_submitted`
- `complete`
- `failed`

#### Auth

`POST /api/register` requires header `X-API-Key`.

Configure one or more keys via env:

```env
REGISTRAR_API_KEYS="key1,key2"
```

#### Register request body

```json
{
	"name": "alice",
	"parent": "bitcoins.vrsc",
	"native_coin": "VRSC",
	"primary_raddress": "RaliceAddress",
	"webhook_url": "https://example.com/hook",
	"webhook_secret": "optional-per-request-secret"
}
```

#### Curl examples

Health check by ticker:

```bash
curl -s "http://localhost:5003/health?native_coin=VRSC"
```

Start registration:

```bash
curl -s -X POST "http://localhost:5003/api/register" \
	-H "Content-Type: application/json" \
	-H "X-API-Key: key1" \
	-d '{
		"name": "alice",
		"parent": "bitcoins.vrsc",
		"native_coin": "VRSC",
		"primary_raddress": "RaliceAddress",
		"webhook_url": "https://example.com/hook",
		"webhook_secret": "my-webhook-secret"
	}'
```

Check status:

```bash
curl -s "http://localhost:5003/api/status/<request_id>"
```

Requeue webhook delivery (terminal requests only):

```bash
curl -s -X POST "http://localhost:5003/api/webhook/requeue/<request_id>" \
	-H "X-API-Key: key1"
```

List recent failed registrations (ops visibility):

```bash
curl -s "http://localhost:5003/api/registrations/failures?limit=20" \
	-H "X-API-Key: key1"
```

Monitoring-friendly example (count + ids):

```bash
curl -s "http://localhost:5003/api/registrations/failures?limit=50" \
	-H "X-API-Key: key1" | jq '{count, ids: [.items[].id]}'
```

Ops triage example (errors + retry fields):

```bash
curl -s "http://localhost:5003/api/registrations/failures?limit=50" \
	-H "X-API-Key: key1" | jq '.items[] | {id, error_message, attempts, next_retry_at, webhook_last_error}'
```

### Worker operation

Run one sweep manually:

```bash
uv run python worker.py
```

Cron example (every minute):

```cron
* * * * * cd /home/mylo/dev/sf/svc-idcreate && /home/mylo/.local/bin/uv run python worker.py >> /var/log/svc-idcreate-worker.log 2>&1
```

### Webhook delivery behavior

When status reaches `complete` or `failed`, worker attempts POST delivery to `webhook_url`.

Delivery headers:

- `Content-Type: application/json`
- `X-Webhook-Event: registration.complete` or `registration.failed`
- `X-Webhook-Signature: sha256=<hmac>` when a secret is available

Secret resolution order:

1. `webhook_secret` from registration request
2. fallback env `WEBHOOK_SIGNING_SECRET`

Webhook retry env settings:

```env
WEBHOOK_TIMEOUT_SECONDS=5
WEBHOOK_MAX_RETRIES=5
WEBHOOK_RETRY_BASE_SECONDS=15
```

### Webhook receiver verification (FastAPI example)

Use this pattern on the receiving server to verify `X-Webhook-Signature`.

```python
import hmac
import hashlib
import json
from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()
WEBHOOK_SECRET = "my-webhook-secret"


@app.post("/webhooks/verusid")
async def verusid_webhook(request: Request, x_webhook_signature: str | None = Header(default=None)):
	raw_body = await request.body()
	payload = json.loads(raw_body)

	expected = "sha256=" + hmac.new(
		WEBHOOK_SECRET.encode("utf-8"),
		json.dumps(payload, sort_keys=True).encode("utf-8"),
		hashlib.sha256,
	).hexdigest()

	if not x_webhook_signature or not hmac.compare_digest(x_webhook_signature, expected):
		raise HTTPException(status_code=403, detail="Invalid webhook signature")

	# process payload here
	return {"ok": True}
```

Quick local receiver test:

```bash
curl -s -X POST "http://localhost:8001/webhooks/verusid" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: sha256=<signature>" \
  -d '{"event":"registration.complete","request_id":"abc"}'
```

### Core env settings

```env
REGISTRAR_DB_PATH=registrar.db
REGISTRAR_API_KEYS=key1,key2
SOURCE_OF_FUNDS=RsourceFundsAddr
WORKER_MAX_RETRIES=5
WORKER_RETRY_BASE_SECONDS=15
WEBHOOK_TIMEOUT_SECONDS=5
WEBHOOK_MAX_RETRIES=5
WEBHOOK_RETRY_BASE_SECONDS=15
WEBHOOK_SIGNING_SECRET=
HEALTH_RPC_DAEMON=verusd_vrsc
```

### Tests with uv

```bash
uv run pytest -q
uv run pytest tests/test_worker.py -q
```

