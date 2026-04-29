# MCP Server (Python)

This server exposes MCP tools on top of the identity service:

- `create_identity`
- `get_identity_request_status`
- `wait_for_identity_completion`
- `list_recent_identity_failures`
- `requeue_identity_webhook`

## Quickstart (Hermes-first)

Use this flow if Hermes (Nous Research) is your first MCP-enabled agent runtime.

### 1) Install dependencies

From repo root:

```bash
uv sync
```

### 2) Start the API + worker (service side)

Terminal A:

```bash
uv run fastapi dev id_create_service.py --port 5003
```

Terminal B:

```bash
uv run python worker.py
```

### 3) Start MCP server (tool side)

Terminal C:

```bash
IDCREATE_BASE_URL="http://localhost:5003" \
IDCREATE_API_KEY="key1" \
IDCREATE_TIMEOUT_SECONDS="15" \
uv run python mcp_server/python/server.py
```

### 4) Connect Hermes runtime to this MCP server

In your Hermes MCP host/client configuration, register a local stdio server command:

```bash
uv run python mcp_server/python/server.py
```

Pass these environment variables through the MCP host config:

- `IDCREATE_BASE_URL`
- `IDCREATE_API_KEY`
- `IDCREATE_TIMEOUT_SECONDS`

Note: exact config shape depends on the Hermes host app you are using, but the command and env values above are the important parts.

### 5) Verify with a simple tool call

Recommended first call sequence in your Hermes agent:

1. `create_identity`
2. `wait_for_identity_completion`
3. `get_identity_request_status` (optional final check)

If you want ops visibility/recovery:

1. `list_recent_identity_failures`
2. `requeue_identity_webhook`

## Environment variables

```env
IDCREATE_BASE_URL=http://localhost:5003
IDCREATE_API_KEY=key1
IDCREATE_TIMEOUT_SECONDS=15
```

## Run with uv

From repo root:

```bash
IDCREATE_BASE_URL="http://localhost:5003" \
IDCREATE_API_KEY="key1" \
IDCREATE_TIMEOUT_SECONDS="15" \
uv run python mcp_server/python/server.py
```

## Smoke and focused tests

```bash
uv run pytest tests/test_mcp_tools.py tests/test_mcp_server_smoke.py -q
```

## Operational notes

- Keep `IDCREATE_API_KEY` in MCP runtime environment only.
- Use bounded polling values when calling `wait_for_identity_completion`.
- If startup fails with `ModuleNotFoundError: No module named 'mcp'`, run `uv sync` again.
