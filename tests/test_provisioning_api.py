"""
tests/test_provisioning_api.py

TDD Red phase: write the tests that define the expected provisioning API behaviour.
"""

import os
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

# Import the service module (needs env set before import)
import id_create_service


# ─── Fixtures ──────────────────────────────────────────────────────────────────

class _FakeRpcConnection:
    def register_name_commitment(self, name, primary_raddress, referral_id, parent, source_of_funds):
        return {
            "txid": "txid-rnc-provision-123",
            "namereservation": {
                "name": name,
                "salt": "abc456",
            },
        }

    def get_raw_transaction(self, txid, verbose=1):
        return {"confirmations": 1, "txid": txid}

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        return "txid-idr-provision-456"


class _FakeProvisioningAdapter:
    def base58check_encode(self, data_hex: str, version: int = 0) -> str:
        # Produce stable, i-address-like ids without external dependencies.
        return f"i{data_hex[:33]}"

    def build_challenge(self, input_data: dict) -> dict:
        challenge_json = {
            "name": input_data["name"],
            "parent": input_data["parent"],
            "system_id": input_data["system_id"],
            "challenge_id": input_data["challenge_id"],
            "created_at": input_data["created_at"],
            "salt": input_data["salt"],
        }
        return {
            "challenge_id": input_data["challenge_id"],
            "challenge_hex": "00" * 64,
            "challenge_json": challenge_json,
            "deeplink_uri": f"verusid://challenge/{input_data['challenge_id']}",
            "vdxfkey": "iFakeVdxfKey",
        }

    def verify_request(self, request_json: dict) -> dict:
        challenge = request_json.get("challenge") if isinstance(request_json, dict) else None
        parsed = challenge if isinstance(challenge, dict) else request_json
        return {
            "challenge_id": parsed.get("challenge_id"),
            "challenge_hash_hex": "11" * 32,
            "signing_address": request_json.get("signing_address"),
            "name": parsed.get("name"),
            "parent": parsed.get("parent"),
            "system_id": parsed.get("system_id"),
            "request_json": request_json,
        }

    def build_response(self, input_data: dict) -> dict:
        state = input_data.get("result_state", "failed")
        return {
            "response_json": {
                "decision": {
                    "decision_id": input_data.get("decision_id"),
                    "result": {
                        "state": state,
                        "error_desc": input_data.get("result_error_desc"),
                    },
                }
            },
            "response_hex": "22" * 64,
            "decision_id": input_data.get("decision_id"),
            "vdxfkey": "iFakeVdxfKey",
        }


# ─── Client factory ─────────────────────────────────────────────────────────────

def _build_client(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")
    monkeypatch.setenv("SIGNING_IDENTITY", "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV")
    monkeypatch.setenv("PROVISIONING_SIGNING_WIF", "KyJZH7XZC7nFfPzzRUehJ3sNMNpBJBG4TnMJYWhxP3XWDPN8k5M3")
    monkeypatch.setenv("DEFAULT_SYSTEM_ID", "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV")

    fake_adapter = _FakeProvisioningAdapter()

    # Fake the RPC (verusd calls in /health, /api/register, /api/provisioning/request)
    monkeypatch.setattr(id_create_service, "_get_rpc_connection", lambda _: _FakeRpcConnection())

    # Reset the cached provisioning engine to pick up fresh env
    from provisioning.router import _reset_engine
    _reset_engine()

    import provisioning.router
    monkeypatch.setattr(provisioning.router, "_build_provisioning_adapter", lambda: fake_adapter)

    # Create the engine fresh with test credentials
    from provisioning.engine import ProvisioningEngine
    engine = ProvisioningEngine(
        signing_identity="i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
        signing_wif="KyJZH7XZC7nFfPzzRUehJ3sNMNpBJBG4TnMJYWhxP3XWDPN8k5M3",
        default_system_id="i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV",
        adapter=fake_adapter,
    )
    # Ensure default_system_id is set directly on the instance (not just via env)
    engine.default_system_id = "i5w5MuNik5NtLcYmNzcvaoixooEebB6MGV"

    # Inject the engine into the router so the endpoints use our test instance
    provisioning.router._engine = engine

    with TestClient(id_create_service.app) as client:
        yield client, engine

    # Clean up
    provisioning.router._engine = None
    _reset_engine()


# ─── Tests: POST /api/provisioning/challenge ──────────────────────────────────

def test_provisioning_challenge_requires_api_key(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
    )
    assert resp.status_code == 403


def test_provisioning_challenge_happy_path(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "challenge_id" in data
    assert "deeplink_uri" in data
    assert "challenge_json" in data
    assert "expires_at" in data
    assert data["name"] == "alice"


def test_provisioning_challenge_stores_challenge_for_later_verification(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "bob",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RBobAddress",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    challenge_id = data["challenge_id"]
    # The challenge should be in the engine's store for later request verification
    assert challenge_id in engine._challenge_store


def test_provisioning_challenge_validates_required_fields(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    # missing name
    resp = client.post(
        "/api/provisioning/challenge",
        json={
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 422


# ─── Tests: POST /api/provisioning/request ─────────────────────────────────────

def test_provisioning_request_requires_api_key(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    resp = client.post(
        "/api/provisioning/request",
        json={"provisioning_request": {"challenge_id": "any"}},
    )
    assert resp.status_code == 403


def test_provisioning_request_returns_400_for_unknown_challenge(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    resp = client.post(
        "/api/provisioning/request",
        json={
            "provisioning_request": {
                "challenge_id": "nonexistent_challenge_id_12345",
                "name": "alice",
            }
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 400


def test_provisioning_request_returns_200_on_success(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    # First create a challenge
    challenge_resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert challenge_resp.status_code == 200, challenge_resp.text
    challenge_data = challenge_resp.json()
    challenge_id = challenge_data["challenge_id"]

    # Submit the provisioning request matching that challenge.
    # The provisioning_request JSON must match LoginConsentProvisioningRequest's
    # expected shape: challenge fields + signing_address.
    resp = client.post(
        "/api/provisioning/request",
        json={
            "provisioning_request": {
                "signing_address": "RYQbUr9WtRRAnMjuddZGryrNEpFEV1h8ph",
                "challenge": challenge_data["challenge_json"],
            }
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "provisioning_response" in data
    assert "request_id" in data  # links to /api/status


def test_provisioning_request_replay_is_blocked(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))

    challenge_resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert challenge_resp.status_code == 200, challenge_resp.text
    challenge_data = challenge_resp.json()

    request_payload = {
        "provisioning_request": {
            "signing_address": "RYQbUr9WtRRAnMjuddZGryrNEpFEV1h8ph",
            "challenge": challenge_data["challenge_json"],
        }
    }

    first = client.post(
        "/api/provisioning/request",
        json=request_payload,
        headers={"X-API-Key": "test-key"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/api/provisioning/request",
        json=request_payload,
        headers={"X-API-Key": "test-key"},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "Challenge has already been consumed"


def test_provisioning_request_returns_400_for_name_mismatch(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    # Create a challenge for "alice"
    challenge_resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    challenge_data = challenge_resp.json()

    # Submit with wrong name in the challenge (charlie instead of alice)
    bad_challenge_json = dict(challenge_data["challenge_json"], name="charlie")
    resp = client.post(
        "/api/provisioning/request",
        json={
            "provisioning_request": {
                "signing_address": "RYQbUr9WtRRAnMjuddZGryrNEpFEV1h8ph",
                "challenge": bad_challenge_json,
            }
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 400


# ─── Tests: GET /api/provisioning/status/{challenge_id} ───────────────────────

def test_provisioning_status_returns_404_for_unknown_challenge(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    resp = client.get("/api/provisioning/status/nonexistent123")
    assert resp.status_code == 404


def test_provisioning_status_returns_current_state(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))
    # Create a challenge
    challenge_resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    challenge_id = challenge_resp.json()["challenge_id"]

    resp = client.get(f"/api/provisioning/status/{challenge_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["challenge_id"] == challenge_id
    assert data["status"] in ("pending", "complete", "failed")


def test_provisioning_status_survives_engine_reset(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))

    challenge_resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert challenge_resp.status_code == 200
    challenge_id = challenge_resp.json()["challenge_id"]

    from provisioning.router import _reset_engine
    _reset_engine()

    status_resp = client.get(f"/api/provisioning/status/{challenge_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["challenge_id"] == challenge_id
    assert data["status"] == "pending"


def test_provisioning_request_verification_survives_engine_reset(monkeypatch, tmp_path):
    client, engine = next(_build_client(monkeypatch, tmp_path))

    challenge_resp = client.post(
        "/api/provisioning/challenge",
        json={
            "name": "alice",
            "parent": "i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4",
            "primary_raddress": "RTestAddress123",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert challenge_resp.status_code == 200
    challenge_data = challenge_resp.json()

    from provisioning.router import _reset_engine
    _reset_engine()

    request_resp = client.post(
        "/api/provisioning/request",
        json={
            "provisioning_request": {
                "signing_address": "RYQbUr9WtRRAnMjuddZGryrNEpFEV1h8ph",
                "challenge": challenge_data["challenge_json"],
            }
        },
        headers={"X-API-Key": "test-key"},
    )
    assert request_resp.status_code == 200, request_resp.text


# ─── Tests: GET /health ────────────────────────────────────────────────────────

def test_provisioning_health_endpoint_exists(monkeypatch, tmp_path):
    """Sanity check: the app's /health endpoint responds (unrelated to provisioning)."""
    client, engine = next(_build_client(monkeypatch, tmp_path))
    resp = client.get("/health")
    # Returns 503 when no real RPC is available — still proves the endpoint exists
    assert resp.status_code in (200, 503)
