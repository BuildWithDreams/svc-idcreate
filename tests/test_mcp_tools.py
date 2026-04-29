import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clients" / "python"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "mcp_server" / "python"))

from idcreate_client import IdCreateApiError
import tools


class _FakeClient:
    def __init__(self):
        self.calls = []

    def create_identity(self, **kwargs):
        self.calls.append(("create_identity", kwargs))
        return {"request_id": "req-1", "status": "pending_rnc_confirm"}

    def get_identity_request_status(self, request_id):
        self.calls.append(("get_identity_request_status", {"request_id": request_id}))
        return {"request_id": request_id, "status": "complete"}


def test_create_identity_tool_success(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(tools, "_client", lambda: fake)

    result = tools.create_identity_tool(
        name="alice",
        parent="bitcoins.vrsc",
        native_coin="VRSC",
        primary_raddress="RaliceAddress",
    )

    assert result["request_id"] == "req-1"
    assert fake.calls[0][0] == "create_identity"
    assert fake.calls[0][1]["name"] == "alice"


def test_get_identity_request_status_tool_success(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(tools, "_client", lambda: fake)

    result = tools.get_identity_request_status_tool("req-123")

    assert result["status"] == "complete"
    assert fake.calls[0][0] == "get_identity_request_status"


def test_create_identity_tool_maps_client_error(monkeypatch):
    class _FailClient:
        def create_identity(self, **kwargs):
            raise IdCreateApiError(503, "rpc unavailable", {"detail": "rpc unavailable"})

    monkeypatch.setattr(tools, "_client", lambda: _FailClient())

    try:
        tools.create_identity_tool(
            name="alice",
            parent="bitcoins.vrsc",
            native_coin="VRSC",
            primary_raddress="RaliceAddress",
        )
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "status=503" in str(exc)


def test_wait_for_identity_completion_tool_returns_terminal_complete(monkeypatch):
    statuses = iter(
        [
            {"request_id": "req-1", "status": "pending_rnc_confirm"},
            {"request_id": "req-1", "status": "ready_for_idr"},
            {"request_id": "req-1", "status": "complete"},
        ]
    )

    class _Clock:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = _Clock()
    monkeypatch.setattr(tools, "get_identity_request_status_tool", lambda request_id: next(statuses))
    monkeypatch.setattr(tools.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(tools.time, "sleep", clock.sleep)

    result = tools.wait_for_identity_completion_tool("req-1", timeout_seconds=30, poll_seconds=2)

    assert result["status"] == "complete"


def test_wait_for_identity_completion_tool_returns_terminal_failed(monkeypatch):
    statuses = iter(
        [
            {"request_id": "req-1", "status": "pending_rnc_confirm"},
            {"request_id": "req-1", "status": "failed", "error": "insufficient funds"},
        ]
    )

    class _Clock:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = _Clock()
    monkeypatch.setattr(tools, "get_identity_request_status_tool", lambda request_id: next(statuses))
    monkeypatch.setattr(tools.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(tools.time, "sleep", clock.sleep)

    result = tools.wait_for_identity_completion_tool("req-1", timeout_seconds=30, poll_seconds=2)

    assert result["status"] == "failed"
    assert result["error"] == "insufficient funds"


def test_wait_for_identity_completion_tool_times_out(monkeypatch):
    class _Clock:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = _Clock()
    monkeypatch.setattr(
        tools,
        "get_identity_request_status_tool",
        lambda request_id: {"request_id": request_id, "status": "pending_rnc_confirm"},
    )
    monkeypatch.setattr(tools.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(tools.time, "sleep", clock.sleep)

    try:
        tools.wait_for_identity_completion_tool("req-timeout", timeout_seconds=3, poll_seconds=1)
        assert False, "Expected TimeoutError"
    except TimeoutError as exc:
        assert "req-timeout" in str(exc)


def test_wait_for_identity_completion_tool_rejects_invalid_polling_args():
    try:
        tools.wait_for_identity_completion_tool("req-1", timeout_seconds=0)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "timeout_seconds" in str(exc)

    try:
        tools.wait_for_identity_completion_tool("req-1", poll_seconds=0)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "poll_seconds" in str(exc)


def test_list_recent_identity_failures_tool_success(monkeypatch):
    class _FakeFailureClient:
        def __init__(self):
            self.calls = []

        def list_recent_identity_failures(self, limit=20):
            self.calls.append(("list_recent_identity_failures", {"limit": limit}))
            return {"count": 1, "items": [{"request_id": "req-f1", "status": "failed"}]}

    fake = _FakeFailureClient()
    monkeypatch.setattr(tools, "_client", lambda: fake)

    result = tools.list_recent_identity_failures_tool(limit=10)

    assert result["count"] == 1
    assert result["items"][0]["request_id"] == "req-f1"
    assert fake.calls[0][0] == "list_recent_identity_failures"
    assert fake.calls[0][1]["limit"] == 10


def test_list_recent_identity_failures_tool_maps_client_error(monkeypatch):
    class _FailFailureClient:
        def list_recent_identity_failures(self, limit=20):
            raise IdCreateApiError(503, "rpc unavailable", {"detail": "rpc unavailable"})

    monkeypatch.setattr(tools, "_client", lambda: _FailFailureClient())

    try:
        tools.list_recent_identity_failures_tool()
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "status=503" in str(exc)


def test_requeue_identity_webhook_tool_success(monkeypatch):
    class _FakeRequeueClient:
        def __init__(self):
            self.calls = []

        def requeue_identity_webhook(self, request_id):
            self.calls.append(("requeue_identity_webhook", {"request_id": request_id}))
            return {"request_id": request_id, "status": "idr_submitted", "webhook_delivery_status": "pending"}

    fake = _FakeRequeueClient()
    monkeypatch.setattr(tools, "_client", lambda: fake)

    result = tools.requeue_identity_webhook_tool("req-1")

    assert result["request_id"] == "req-1"
    assert result["webhook_delivery_status"] == "pending"
    assert fake.calls[0][0] == "requeue_identity_webhook"


def test_requeue_identity_webhook_tool_maps_client_error(monkeypatch):
    class _FailRequeueClient:
        def requeue_identity_webhook(self, request_id):
            raise IdCreateApiError(404, "not found", {"detail": "not found"})

    monkeypatch.setattr(tools, "_client", lambda: _FailRequeueClient())

    try:
        tools.requeue_identity_webhook_tool("req-missing")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "status=404" in str(exc)
