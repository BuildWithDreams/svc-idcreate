import json
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest
from provisioning.adapters import HttpProvisioningAdapter


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "provisioning_golden_vectors.json"


def _adapter() -> HttpProvisioningAdapter:
    base_url = os.getenv("PROVISIONING_SERVICE_URL", "http://127.0.0.1:5055")
    timeout_seconds = int(os.getenv("PROVISIONING_HTTP_TIMEOUT_SECONDS", "10"))
    retry_count = int(os.getenv("PROVISIONING_RETRY_COUNT", "0"))
    return HttpProvisioningAdapter(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
    )


def _service_available(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port
    if host is None:
        return False
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def adapter_or_skip() -> HttpProvisioningAdapter:
    adapter = _adapter()
    if not _service_available(adapter.base_url):
        pytest.skip(
            f"Provisioning HTTP service not reachable at {adapter.base_url}; skipping golden-vector contract tests"
        )
    return adapter


def _load_vectors() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_golden_vector_build_challenge_matches_expected(adapter_or_skip):
    vectors = _load_vectors()
    adapter = adapter_or_skip

    result = adapter.build_challenge(vectors["challenge"]["input"])
    expected = vectors["challenge"]["expected"]

    assert result["challenge_id"] == expected["challenge_id"]
    assert result["challenge_hex"] == expected["challenge_hex"]
    assert result["vdxfkey"] == expected["vdxfkey"]
    assert result["deeplink_uri"] == expected["deeplink_uri"]


def test_golden_vector_verify_request_matches_expected(adapter_or_skip):
    vectors = _load_vectors()
    adapter = adapter_or_skip

    result = adapter.verify_request(vectors["request"]["input"])
    expected = vectors["request"]["expected"]

    assert result["challenge_id"] == expected["challenge_id"]
    assert result["challenge_hash_hex"] == expected["challenge_hash_hex"]
    assert result["signing_address"] == expected["signing_address"]
    assert result["name"] == expected["name"]
    assert result["parent"] == expected["parent"]
    assert result["system_id"] == expected["system_id"]


def test_golden_vector_build_response_matches_expected(adapter_or_skip):
    vectors = _load_vectors()
    adapter = adapter_or_skip

    result = adapter.build_response(vectors["response"]["input"])
    expected = vectors["response"]["expected"]

    assert result["response_hex"] == expected["response_hex"]
    assert result["decision_id"] == expected["decision_id"]
    assert result["vdxfkey"] == expected["vdxfkey"]
    assert result["response_json"]["decision"]["result"]["error_desc"] == expected["error_desc"]
    assert result["response_json"]["decision"]["result"]["state"] == expected["state"]
