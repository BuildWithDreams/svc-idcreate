# AGENTS.md - Identity Creation Service

This document provides guidelines and commands for agents working in this repository.

## Project Overview

- **Service**: Identity creation service with async registration workflow
- **Framework**: FastAPI (Python)
- **Package Manager**: uv
- **Database**: SQLite
- **Testing**: pytest
- **Key Files**:
  - `id_create_service.py` - Main FastAPI application
  - `worker.py` - Background worker for advancing registration states
  - `mcp_server/python/` - MCP server for AI tool integrations
  - `clients/python/idcreate_client.py` - Python API client
  - `clients/typescript/` - TypeScript API client

---

## Build/Lint/Test Commands

### Setup and Dependencies

```bash
# Install dependencies
uv sync

# Add a package
uv add <package_name>

# Add dev dependency
uv add --dev pytest

# Remove a package
uv remove <package_name>
```

### Running the Application

```bash
# Run the API server
uv run fastapi dev id_create_service.py --port 5003

# Run the background worker (single sweep)
uv run python worker.py

# Run MCP server
IDCREATE_BASE_URL="http://localhost:5003" \
IDCREATE_API_KEY="key1" \
IDCREATE_TIMEOUT_SECONDS="15" \
uv run python mcp_server/python/server.py
```

### Testing

```bash
# Run all tests
uv run pytest -q

# Run a single test file
uv run pytest tests/test_worker.py -q

# Run a specific test function
uv run pytest tests/test_registration_api.py::test_register_happy_path -v

# Run tests matching a pattern
uv run pytest -k "test_register" -v

# Run with output capture disabled (see print statements)
uv run pytest tests/test_worker.py -q -s

# Run MCP-specific tests
uv run pytest tests/test_mcp_tools.py tests/test_mcp_server_smoke.py -q
```

### API Documentation

- Swagger UI: http://localhost:5003/docs
- OpenAPI JSON: http://localhost:5003/openapi.json

---

## Code Style Guidelines

### General

- **Python Version**: 3.12+
- **Type System**: Use type hints (`str | None`, `dict[str, Any]`, etc.)
- **No formatter/linter enforced**: Write clean, consistent code following these conventions

### Imports

- Use `from x import y` style for standard library and project imports
- Group imports: stdlib → third-party → project (not strictly enforced but recommended)
- Example:
  ```python
  from fastapi import FastAPI, HTTPException, Security
  from pydantic import BaseModel, Field
  from contextlib import asynccontextmanager
  import os
  import json
  ```

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Modules | snake_case | `id_create_service.py` |
| Classes | PascalCase | `RegisterRequest`, `IdCreateApiError` |
| Functions | snake_case | `_get_db_connection`, `process_once` |
| Variables | snake_case | `db_path`, `request_id` |
| Constants | UPPER_SNAKE_CASE | `DAEMON_VERUSD_VRSC`, `TICKER_VRSC` |
| Private functions | prefix with `_` | `_require_api_key`, `_record_retry_or_failure` |

### Docstrings

- Use docstrings for public functions and classes
- Simple one-line docstrings for straightforward cases:
  ```python
  def _get_db_path() -> str:
      return os.getenv("REGISTRAR_DB_PATH", "registrar.db")
  ```
- Multi-line docstrings for complex functions:
  ```python
  def process_once() -> int:
      """Process one worker sweep over pending commitment confirmations.

      Returns the number of rows that were advanced to the next state.
      """
  ```

### Type Hints

- Use modern union syntax (`str | None` instead of `Optional[str]`)
- Use `dict[str, Any]` for flexible dict types
- Return types should be annotated on all functions:
  ```python
  def _get_db_connection() -> sqlite3.Connection:
      ...
  ```

### Error Handling

- **HTTP Errors**: Raise `HTTPException` from FastAPI with appropriate status codes:
  ```python
  raise HTTPException(status_code=404, detail="Request not found")
  raise HTTPException(status_code=503, detail={"status": "degraded", "error": str(e)})
  ```
- **Custom Exceptions**: Define exception classes for API clients:
  ```python
  class IdCreateApiError(Exception):
      def __init__(self, status_code: int, message: str, body: Any = None):
          super().__init__(f"HTTP {status_code}: {message}")
          self.status_code = status_code
          self.message = message
          self.body = body
  ```
- **Worker Errors**: Use exponential backoff retry logic; record failures in DB:
  ```python
  def _record_retry_or_failure(conn, row_id, attempts, error, status):
      max_retries, base_seconds = _retry_config()
      next_attempt = attempts + 1
      if next_attempt >= max_retries:
          # Mark as failed
          ...
  ```
- **RPC Errors**: Catch broad exceptions, log with context, re-raise with user-friendly message
- **Never expose secrets in error messages**

### FastAPI Patterns

- **Lifespan**: Use `@asynccontextmanager` for startup/shutdown:
  ```python
  @asynccontextmanager
  async def lifespan(_: FastAPI):
      _init_db()
      yield
  app = FastAPI(lifespan=lifespan)
  ```
- **Authentication**: Use `Security` with `APIKeyHeader`:
  ```python
  api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
  def _require_api_key(api_key: str | None = Security(api_key_header)):
      if api_key not in valid_keys:
          raise HTTPException(status_code=403, detail="Invalid API key")
      return api_key
  ```
- **Pydantic Models**: Use `BaseModel` with `Field` for request validation:
  ```python
  class RegisterRequest(BaseModel):
      name: str = Field(description="Identity name without parent namespace.")
      webhook_url: str | None = Field(default=None, description="Optional callback URL.")
  ```

### Database Patterns

- Use `sqlite3.Row` row factory for dict-like access:
  ```python
  conn = sqlite3.connect(_get_db_path())
  conn.row_factory = sqlite3.Row
  row = conn.execute("SELECT * FROM registrations WHERE id = ?", (request_id,)).fetchone()
  data = dict(row)  # Convert Row to dict
  ```
- Always close connections (use `with` or explicit `close()`)
- Use parameterized queries (never string concatenation)

### Logging

- Use module-level loggers:
  ```python
  import logging
  logger = logging.getLogger(__name__)
  ```
- Log at appropriate levels: DEBUG for dev, INFO for operations, ERROR for failures
- Never log secrets or passwords

### Security

- API keys via environment variables, never hardcoded
- Webhook secrets stored securely, used for HMAC signature verification
- Mask sensitive data in logs (e.g., passwords)
- Validate all inputs with Pydantic models

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REGISTRAR_DB_PATH` | SQLite database path | `registrar.db` |
| `REGISTRAR_API_KEYS` | Comma-separated API keys | - |
| `SOURCE_OF_FUNDS` | Source of funds address | - |
| `HEALTH_RPC_DAEMON` | Daemon for health checks | `verusd_vrsc` |
| `WORKER_MAX_RETRIES` | Max worker retries | `5` |
| `WEBHOOK_TIMEOUT_SECONDS` | Webhook timeout | `5` |
| `WEBHOOK_MAX_RETRIES` | Max webhook retries | `5` |
| `WEBHOOK_SIGNING_SECRET` | Fallback webhook HMAC secret | - |

---

## Testing Patterns

- Use `pytest` with `monkeypatch` for environment variable mocking
- Use `fastapi.testclient.TestClient` for API testing
- Create fake RPC connections for isolated testing:
  ```python
  class _FakeRpcConnection:
      def register_name_commitment(self, name, primary_raddress, referral_id, parent, source_of_funds):
          return {"txid": "txid-rnc-123", "namereservation": {"name": name, "salt": "abc123"}}
  ```
- Temp directory for test databases via `tmp_path` fixture
- Test file naming: `test_<module_name>.py`
- Test function naming: `test_<function_or_feature>_<scenario>`

---

## File Structure

```
.
├── id_create_service.py       # Main FastAPI app
├── worker.py                   # Background worker
├── main.py                     # Entry point (simple)
├── SFConstants.py              # Constants and config
├── rpc_manager.py             # RPC connection manager
├── verus_node_rpc.py          # Low-level RPC wrapper
├── clients/
│   ├── python/
│   │   ├── idcreate_client.py # Python API client
│   │   └── examples/          # Example scripts
│   └── typescript/            # TypeScript client
├── mcp_server/python/
│   ├── server.py              # MCP server
│   └── tools.py               # MCP tool implementations
└── tests/
    ├── test_registration_api.py
    ├── test_worker.py
    ├── test_mcp_tools.py
    ├── test_mcp_server_smoke.py
    └── ...
```
