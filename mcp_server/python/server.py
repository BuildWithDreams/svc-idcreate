import sys
from pathlib import Path

# Make thin client importable when server runs from repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "clients" / "python"))

from mcp.server.fastmcp import FastMCP
from tools import (
    create_identity_tool,
    get_identity_request_status_tool,
    list_recent_identity_failures_tool,
    requeue_identity_webhook_tool,
    wait_for_identity_completion_tool,
)

mcp = FastMCP("idcreate-service")


@mcp.tool()
def create_identity(
    name: str,
    parent: str,
    native_coin: str,
    primary_raddress: str,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
) -> dict:
    """Create an asynchronous identity registration request.

    Returns request metadata including `request_id` for later status checks.
    """
    return create_identity_tool(
        name=name,
        parent=parent,
        native_coin=native_coin,
        primary_raddress=primary_raddress,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )


@mcp.tool()
def get_identity_request_status(request_id: str) -> dict:
    """Get registration lifecycle state for a previously created request."""
    return get_identity_request_status_tool(request_id)


@mcp.tool()
def wait_for_identity_completion(
    request_id: str,
    timeout_seconds: int = 300,
    poll_seconds: int = 5,
) -> dict:
    """Poll until a request reaches terminal state or timeout.

    Terminal states are `complete` and `failed`.
    """
    return wait_for_identity_completion_tool(
        request_id=request_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


@mcp.tool()
def list_recent_identity_failures(limit: int = 20) -> dict:
    """List recent failed registration requests for ops visibility."""
    return list_recent_identity_failures_tool(limit=limit)


@mcp.tool()
def requeue_identity_webhook(request_id: str) -> dict:
    """Requeue webhook delivery for a terminal identity request."""
    return requeue_identity_webhook_tool(request_id)


if __name__ == "__main__":
    mcp.run()
