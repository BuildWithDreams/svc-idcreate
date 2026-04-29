import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "mcp_server" / "python"))

import server


def test_server_tool_functions_delegate_to_wrappers(monkeypatch):
    calls = []

    def _create_identity_tool(**kwargs):
        calls.append(("create_identity_tool", kwargs))
        return {"request_id": "req-1", "status": "pending_rnc_confirm"}

    def _get_identity_request_status_tool(request_id):
        calls.append(("get_identity_request_status_tool", {"request_id": request_id}))
        return {"request_id": request_id, "status": "ready_for_idr"}

    def _wait_for_identity_completion_tool(request_id, timeout_seconds=300, poll_seconds=5):
        calls.append(
            (
                "wait_for_identity_completion_tool",
                {
                    "request_id": request_id,
                    "timeout_seconds": timeout_seconds,
                    "poll_seconds": poll_seconds,
                },
            )
        )
        return {"request_id": request_id, "status": "complete"}

    def _list_recent_identity_failures_tool(limit=20):
        calls.append(("list_recent_identity_failures_tool", {"limit": limit}))
        return {"count": 1, "items": [{"request_id": "req-f1"}]}

    def _requeue_identity_webhook_tool(request_id):
        calls.append(("requeue_identity_webhook_tool", {"request_id": request_id}))
        return {"request_id": request_id, "webhook_delivery_status": "pending"}

    monkeypatch.setattr(server, "create_identity_tool", _create_identity_tool)
    monkeypatch.setattr(server, "get_identity_request_status_tool", _get_identity_request_status_tool)
    monkeypatch.setattr(server, "wait_for_identity_completion_tool", _wait_for_identity_completion_tool)
    monkeypatch.setattr(server, "list_recent_identity_failures_tool", _list_recent_identity_failures_tool)
    monkeypatch.setattr(server, "requeue_identity_webhook_tool", _requeue_identity_webhook_tool)

    create_result = server.create_identity(
        name="alice",
        parent="bitcoins.vrsc",
        native_coin="VRSC",
        primary_raddress="RaliceAddress",
        webhook_url="https://example.com/hook",
        webhook_secret="secret",
    )
    status_result = server.get_identity_request_status("req-1")
    wait_result = server.wait_for_identity_completion("req-1", timeout_seconds=60, poll_seconds=2)
    failures_result = server.list_recent_identity_failures(limit=5)
    requeue_result = server.requeue_identity_webhook("req-1")

    assert create_result["request_id"] == "req-1"
    assert status_result["status"] == "ready_for_idr"
    assert wait_result["status"] == "complete"
    assert failures_result["count"] == 1
    assert requeue_result["webhook_delivery_status"] == "pending"

    assert calls[0][0] == "create_identity_tool"
    assert calls[0][1]["name"] == "alice"
    assert calls[1] == ("get_identity_request_status_tool", {"request_id": "req-1"})
    assert calls[2] == (
        "wait_for_identity_completion_tool",
        {"request_id": "req-1", "timeout_seconds": 60, "poll_seconds": 2},
    )
    assert calls[3] == ("list_recent_identity_failures_tool", {"limit": 5})
    assert calls[4] == ("requeue_identity_webhook_tool", {"request_id": "req-1"})
