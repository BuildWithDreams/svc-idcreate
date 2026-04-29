import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "clients" / "python"))

from idcreate_client import IdCreateApiError, IdCreateClient


def _client() -> IdCreateClient:
    base_url = os.getenv("IDCREATE_BASE_URL", "http://localhost:5003")
    api_key = os.getenv("IDCREATE_API_KEY", "")
    timeout_seconds = int(os.getenv("IDCREATE_TIMEOUT_SECONDS", "15"))
    return IdCreateClient(base_url=base_url, api_key=api_key, timeout_seconds=timeout_seconds)


def create_identity_tool(
    name: str,
    parent: str,
    native_coin: str,
    primary_raddress: str,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
) -> dict:
    try:
        return _client().create_identity(
            name=name,
            parent=parent,
            native_coin=native_coin,
            primary_raddress=primary_raddress,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
        )
    except IdCreateApiError as exc:
        raise ValueError(f"create_identity failed: status={exc.status_code} message={exc.message} body={exc.body}")


def get_identity_request_status_tool(request_id: str) -> dict:
    try:
        return _client().get_identity_request_status(request_id)
    except IdCreateApiError as exc:
        raise ValueError(
            f"get_identity_request_status failed: status={exc.status_code} message={exc.message} body={exc.body}"
        )


def wait_for_identity_completion_tool(
    request_id: str,
    timeout_seconds: int = 300,
    poll_seconds: int = 5,
) -> dict:
    if timeout_seconds < 1:
        raise ValueError("wait_for_identity_completion failed: timeout_seconds must be >= 1")
    if poll_seconds < 1:
        raise ValueError("wait_for_identity_completion failed: poll_seconds must be >= 1")

    deadline = time.monotonic() + timeout_seconds
    while True:
        result = get_identity_request_status_tool(request_id)
        status = str(result.get("status", "")).lower()
        if status in {"complete", "failed"}:
            return result

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"wait_for_identity_completion timed out: request_id={request_id} timeout_seconds={timeout_seconds}"
            )

        time.sleep(poll_seconds)


def list_recent_identity_failures_tool(limit: int = 20) -> dict:
    try:
        return _client().list_recent_identity_failures(limit=limit)
    except IdCreateApiError as exc:
        raise ValueError(
            f"list_recent_identity_failures failed: status={exc.status_code} message={exc.message} body={exc.body}"
        )


def requeue_identity_webhook_tool(request_id: str) -> dict:
    try:
        return _client().requeue_identity_webhook(request_id)
    except IdCreateApiError as exc:
        raise ValueError(
            f"requeue_identity_webhook failed: status={exc.status_code} message={exc.message} body={exc.body}"
        )
