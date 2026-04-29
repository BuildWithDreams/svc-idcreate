import io
import json
import pathlib
import sys
from urllib import error as urllib_error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "clients" / "python"))

from idcreate_client import IdCreateApiError, IdCreateClient


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_create_identity_sends_expected_payload_and_headers(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["timeout"] = timeout
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"request_id": "req-1", "status": "pending_rnc_confirm"})

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003", api_key="key1", timeout_seconds=7)
    result = client.create_identity(
        name="alice",
        parent="bitcoins.vrsc",
        native_coin="VRSC",
        primary_raddress="RaliceAddress",
        webhook_url="https://example.com/hook",
        webhook_secret="secret",
    )

    assert result["request_id"] == "req-1"
    assert captured["url"] == "http://localhost:5003/api/register"
    assert captured["method"] == "POST"
    assert captured["timeout"] == 7
    assert captured["headers"]["X-api-key"] == "key1"
    assert captured["body"]["name"] == "alice"
    assert captured["body"]["webhook_url"] == "https://example.com/hook"


def test_http_error_maps_to_idcreate_api_error(monkeypatch):
    def _fake_urlopen(req, timeout):
        raise urllib_error.HTTPError(
            url=req.full_url,
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"rpc unavailable"}'),
        )

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003", api_key="key1")

    try:
        client.get_identity_request_status("req-1")
        assert False, "Expected IdCreateApiError"
    except IdCreateApiError as exc:
        assert exc.status_code == 503
        assert "rpc unavailable" in exc.message


def test_transport_error_maps_to_idcreate_api_error(monkeypatch):
    def _fake_urlopen(req, timeout):
        raise urllib_error.URLError("connection refused")

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003")

    try:
        client.health()
        assert False, "Expected IdCreateApiError"
    except IdCreateApiError as exc:
        assert exc.status_code == 0
        assert "Transport error" in exc.message


def test_create_storage_upload_sends_expected_payload(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["timeout"] = timeout
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"upload_id": "upl-1", "status": "pending", "chunk_count": 1})

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003", api_key="key1", timeout_seconds=9)
    result = client.create_storage_upload(
        name="trial1",
        parent="filestorage",
        native_coin="VRSC",
        primary_raddress="RaliceAddress",
        file_path="/tmp/book.json",
        mime_type="application/json",
        chunk_size_bytes=999000,
    )

    assert result["upload_id"] == "upl-1"
    assert captured["url"] == "http://localhost:5003/api/storage/upload"
    assert captured["method"] == "POST"
    assert captured["timeout"] == 9
    assert captured["headers"]["X-api-key"] == "key1"
    assert captured["body"]["name"] == "trial1"
    assert captured["body"]["file_path"] == "/tmp/book.json"


def test_get_storage_upload_status_hits_expected_path(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _FakeResponse({"upload": {"id": "upl-1"}, "chunks": []})

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003", api_key="key1")
    result = client.get_storage_upload_status("upl-1")

    assert result["upload"]["id"] == "upl-1"
    assert captured["url"] == "http://localhost:5003/api/storage/upload/upl-1"
    assert captured["method"] == "GET"


def test_start_storage_upload_hits_expected_path(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"upload_id": "upl-1", "status": "uploading"})

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003", api_key="key1")
    result = client.start_storage_upload("upl-1")

    assert result["status"] == "uploading"
    assert captured["url"] == "http://localhost:5003/api/storage/upload/upl-1/start"
    assert captured["method"] == "POST"
    assert captured["body"] == {}


def test_retry_storage_upload_hits_expected_path(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"upload_id": "upl-1", "status": "pending"})

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003", api_key="key1")
    result = client.retry_storage_upload("upl-1")

    assert result["status"] == "pending"
    assert captured["url"] == "http://localhost:5003/api/storage/upload/upl-1/retry"
    assert captured["method"] == "POST"
    assert captured["body"] == {}


def test_retrieve_storage_upload_hits_expected_path(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _FakeResponse({"upload_id": "upl-1", "sha256_verified": True, "size_bytes": 12, "content_hex": "68656c6c6f"})

    monkeypatch.setattr("idcreate_client.urllib_request.urlopen", _fake_urlopen)

    client = IdCreateClient(base_url="http://localhost:5003")
    result = client.retrieve_storage_upload("upl-1")

    assert result["sha256_verified"] is True
    assert captured["url"] == "http://localhost:5003/api/storage/retrieve/upl-1"
    assert captured["method"] == "GET"
