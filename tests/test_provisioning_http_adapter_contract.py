import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from provisioning.adapters import HttpProvisioningAdapter


class _MockProvisioningHandler(BaseHTTPRequestHandler):
    routes = {}
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else ""

        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"_raw": raw}

        _MockProvisioningHandler.received.append({
            "path": self.path,
            "payload": payload,
            "headers": dict(self.headers),
        })

        route = _MockProvisioningHandler.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "not_found"}).encode("utf-8"))
            return

        status, body, content_type = route(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        if isinstance(body, str):
            self.wfile.write(body.encode("utf-8"))
        else:
            self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, format, *args):
        return


@pytest.fixture
def mock_http_server():
    _MockProvisioningHandler.routes = {}
    _MockProvisioningHandler.received = []

    server = HTTPServer(("127.0.0.1", 0), _MockProvisioningHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url, _MockProvisioningHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_adapter_contract_happy_paths(mock_http_server):
    base_url, handler = mock_http_server

    handler.routes = {
        "/v1/provisioning/challenge/build": lambda payload: (
            200,
            {
                "challenge_id": payload["challenge_id"],
                "challenge_hex": "deadbeef",
            },
            "application/json",
        ),
        "/v1/provisioning/request/verify": lambda payload: (
            200,
            {
                "challenge_id": payload["request_json"]["challenge"]["challenge_id"],
                "signing_address": payload["request_json"]["signing_address"],
            },
            "application/json",
        ),
        "/v1/provisioning/response/build": lambda payload: (
            200,
            {
                "response_json": {"state": payload["result_state"]},
                "response_hex": "beadfeed",
            },
            "application/json",
        ),
        "/v1/base58check/encode": lambda payload: (
            200,
            {
                "result": f"i-encoded-{payload['version']}"
            },
            "application/json",
        ),
    }

    adapter = HttpProvisioningAdapter(base_url=base_url, timeout_seconds=2)

    challenge_result = adapter.build_challenge(
        {
            "challenge_id": "iABC",
            "name": "alice",
            "system_id": "iSYS",
            "parent": "iPARENT",
            "created_at": 1700000000,
            "salt": "iSALT",
        }
    )
    assert challenge_result["challenge_hex"] == "deadbeef"

    verify_result = adapter.verify_request(
        {
            "signing_address": "Rsigning",
            "challenge": {"challenge_id": "iABC"},
        }
    )
    assert verify_result["challenge_id"] == "iABC"
    assert verify_result["signing_address"] == "Rsigning"

    response_result = adapter.build_response(
        {
            "system_id": "iSYS",
            "signing_id": "iSYS",
            "signing_address": "Rsigning",
            "decision_id": "iABC",
            "result_state": "failed",
        }
    )
    assert response_result["response_hex"] == "beadfeed"

    encoded = adapter.base58check_encode("0011", version=102)
    assert encoded == "i-encoded-102"

    paths = [r["path"] for r in handler.received]
    assert paths == [
        "/v1/provisioning/challenge/build",
        "/v1/provisioning/request/verify",
        "/v1/provisioning/response/build",
        "/v1/base58check/encode",
    ]


def test_http_adapter_contract_http_error(mock_http_server):
    base_url, handler = mock_http_server

    handler.routes = {
        "/v1/provisioning/challenge/build": lambda payload: (
            500,
            {"error": "boom"},
            "application/json",
        )
    }

    adapter = HttpProvisioningAdapter(base_url=base_url, timeout_seconds=2)

    with pytest.raises(RuntimeError, match="HTTP adapter error"):
        adapter.build_challenge(
            {
                "challenge_id": "iABC",
                "name": "alice",
                "system_id": "iSYS",
                "parent": "iPARENT",
                "created_at": 1700000000,
            }
        )


def test_http_adapter_contract_invalid_json_response(mock_http_server):
    base_url, handler = mock_http_server

    handler.routes = {
        "/v1/base58check/encode": lambda payload: (
            200,
            "not-json",
            "text/plain",
        )
    }

    adapter = HttpProvisioningAdapter(base_url=base_url, timeout_seconds=2)

    with pytest.raises(RuntimeError, match="invalid JSON"):
        adapter.base58check_encode("0011", version=102)
