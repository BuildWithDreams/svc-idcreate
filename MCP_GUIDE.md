# MCP Integration Guide

This guide describes the Python MCP server that wraps the identity service so agents and clients can create identities safely.

## Goal

Provide a clean MCP tool interface on top of existing HTTP endpoints:

- `POST /api/register`
- `GET /api/status/{request_id}`
- `GET /api/registrations/failures`
- `POST /api/webhook/requeue/{request_id}`

## Implementation Status

Completed in current codebase:

- `create_identity`
- `get_identity_request_status`
- `wait_for_identity_completion`
- `list_recent_identity_failures`
- `requeue_identity_webhook`

Coverage now includes:

- Focused tool tests in `tests/test_mcp_tools.py`
- MCP server smoke test in `tests/test_mcp_server_smoke.py`

## Recommended MCP Tools

## 1) create_identity

Purpose:
- Start asynchronous registration.

Maps to:
- `POST /api/register`

Inputs:
- `name` (string)
- `parent` (string)
- `native_coin` (string)
- `primary_raddress` (string)
- `webhook_url` (optional string)
- `webhook_secret` (optional string)

Output:
- `request_id`
- `status`
- `daemon`
- `native_coin`
- `txid_rnc`

## 2) get_identity_request_status

Purpose:
- Retrieve current lifecycle state.

Maps to:
- `GET /api/status/{request_id}`

Inputs:
- `request_id` (string)

Output:
- Full persisted status record.

## 3) wait_for_identity_completion

Purpose:
- Convenience polling helper for agent workflows.

Behavior:
- Calls `get_identity_request_status` every `poll_seconds`.
- Stops on terminal status (`complete` or `failed`) or timeout.

Inputs:
- `request_id` (string)
- `timeout_seconds` (default 300)
- `poll_seconds` (default 5)

Output:
- Final status payload.
- Timeout error if terminal state not reached.

## 4) list_recent_identity_failures

Purpose:
- Ops visibility and monitoring.

Maps to:
- `GET /api/registrations/failures?limit=...`

Inputs:
- `limit` (default 20)

Output:
- `count`
- `items[]`

## 5) requeue_identity_webhook

Purpose:
- Recover from webhook delivery failures for terminal requests.

Maps to:
- `POST /api/webhook/requeue/{request_id}`

Inputs:
- `request_id`

Output:
- Requeue confirmation payload.

## MCP Security Model

- Keep service API key only in MCP server environment (never client-side).
- Add user-level auth at MCP boundary before forwarding calls.
- Rate limit `create_identity` per user/client.
- Log request correlation IDs (`request_id`) in MCP logs.
- Rotate service API keys and support multiple active keys.

## Error Mapping (Service -> MCP)

Suggested normalized errors:

- 400/422 -> `ValidationError`
- 403 -> `AuthorizationError`
- 404 -> `NotFoundError`
- 409 -> `ConflictError`
- 503 -> `UnavailableError`
- timeout/network -> `TransportError`

Include original HTTP status and response body in MCP error metadata.

## Minimal MCP Tool Contracts

## create_identity schema

Input schema:

```json
{
  "type": "object",
  "required": ["name", "parent", "native_coin", "primary_raddress"],
  "properties": {
    "name": { "type": "string" },
    "parent": { "type": "string" },
    "native_coin": { "type": "string" },
    "primary_raddress": { "type": "string" },
    "webhook_url": { "type": "string" },
    "webhook_secret": { "type": "string" }
  }
}
```

Output schema:

```json
{
  "type": "object",
  "properties": {
    "request_id": { "type": "string" },
    "status": { "type": "string" },
    "daemon": { "type": "string" },
    "native_coin": { "type": "string" },
    "txid_rnc": { "type": ["string", "null"] }
  }
}
```

## wait_for_identity_completion schema

Input schema:

```json
{
  "type": "object",
  "required": ["request_id"],
  "properties": {
    "request_id": { "type": "string" },
    "timeout_seconds": { "type": "integer", "minimum": 1, "default": 300 },
    "poll_seconds": { "type": "integer", "minimum": 1, "default": 5 }
  }
}
```

## Reference HTTP examples

Start registration:

```bash
curl -s -X POST "http://localhost:5003/api/register" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: key1" \
  -d '{
    "name": "alice",
    "parent": "bitcoins.vrsc",
    "native_coin": "VRSC",
    "primary_raddress": "RaliceAddress"
  }'
```

Get status:

```bash
curl -s "http://localhost:5003/api/status/<request_id>"
```

List recent failures:

```bash
curl -s "http://localhost:5003/api/registrations/failures?limit=20" \
  -H "X-API-Key: key1"
```

## Suggested Implementation Order

1. Implement MCP tools: `create_identity`, `get_identity_request_status`. ✅
2. Add `wait_for_identity_completion` helper. ✅
3. Add ops tools: `list_recent_identity_failures`, `requeue_identity_webhook`. ✅
4. Add auth/rate limiting at MCP layer.
5. Add telemetry and alerting around failure/error rates.

## Operational Tips

Run and dependency management:

- `mcp` is now a project dependency in `pyproject.toml`; install/update with `uv sync`.
- Run the server from repo root so local imports resolve as expected:
  - `uv run python mcp_server/python/server.py`
- Use environment variables for runtime wiring:
  - `IDCREATE_BASE_URL` (target API URL)
  - `IDCREATE_API_KEY` (service API key)
  - `IDCREATE_TIMEOUT_SECONDS` (per-request timeout)

Reliability and safety:

- Keep `IDCREATE_API_KEY` only in MCP runtime environment, never in client-side configs.
- Prefer `wait_for_identity_completion` for agent flows, but set bounded `timeout_seconds` and sane `poll_seconds` to avoid runaway polling.
- Treat `list_recent_identity_failures` as your first ops triage tool, then run `requeue_identity_webhook` only for terminal requests that failed webhook delivery.

Testing and release checks:

- Fast MCP-only confidence check:
  - `uv run pytest tests/test_mcp_tools.py tests/test_mcp_server_smoke.py -q`
- Full regression before deploy:
  - `uv run pytest -q`

Troubleshooting quick checks:

- `ModuleNotFoundError: No module named 'mcp'`:
  - Run `uv sync` to ensure dependency installation.
- MCP call gets `AuthorizationError`/403-equivalent from service:
  - Verify `IDCREATE_API_KEY` matches one of `REGISTRAR_API_KEYS` on API service.
- Repeated timeout on wait helper:
  - Check worker is running and draining lifecycle states; inspect `/api/status/{request_id}` directly.

## Current Python MCP files

Python MCP implementation is available at:

- `mcp_server/python/server.py`
- `mcp_server/python/tools.py`
- `mcp_server/python/README.md`
