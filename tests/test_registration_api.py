import os
import pathlib
import sys
import sqlite3
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import id_create_service


class _FakeRpcConnection:
    def register_name_commitment(self, name, primary_raddress, referral_id, parent, source_of_funds):
        return {
            "txid": "txid-rnc-123",
            "namereservation": {
                "name": name,
                "salt": "abc123",
            },
        }


def _build_client(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")

    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: "verusd_vrsc")
    monkeypatch.setattr(id_create_service, "_get_rpc_connection", lambda _: _FakeRpcConnection())

    with TestClient(id_create_service.app) as client:
        yield client


def test_register_happy_path(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
    }
    resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending_rnc_confirm"
    assert data["request_id"]



def test_register_rejects_missing_api_key(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
    }
    resp = client.post("/api/register", json=payload)

    assert resp.status_code == 403



def test_register_returns_503_for_unknown_native_coin(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))
    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: None)

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "UNKNOWN",
        "primary_raddress": "RaliceAddress",
    }
    resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 503


def test_register_rejects_parent_not_in_allowlist(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))
    monkeypatch.setenv("REGISTRAR_ALLOWED_PARENTS", "bitcoins.vrsc,private.vrsc")

    payload = {
        "name": "alice",
        "parent": "untrusted.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
    }
    resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["requested_parent"] == "untrusted.vrsc"
    assert "bitcoins.vrsc" in detail["allowed_parents"]


def test_register_accepts_parent_from_parent_env(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))
    monkeypatch.setenv("PARENT", "bitcoins.vrsc")

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
    }
    resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202



def test_status_returns_saved_registration(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
    }

    create_resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )
    request_id = create_resp.json()["request_id"]

    status_resp = client.get(f"/api/status/{request_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["id"] == request_id



def test_status_returns_404_for_unknown_id(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/api/status/not-found")
    assert resp.status_code == 404


def test_register_persists_webhook_fields(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
        "webhook_url": "https://example.com/hook",
        "webhook_secret": "my-secret",
    }

    create_resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )
    request_id = create_resp.json()["request_id"]

    status_resp = client.get(f"/api/status/{request_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["webhook_url"] == "https://example.com/hook"
    assert data["webhook_secret"] == "my-secret"
    assert data["webhook_delivered"] == 0


def test_requeue_webhook_resets_delivery_fields(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
        "webhook_url": "https://example.com/hook",
        "webhook_secret": "my-secret",
    }
    create_resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )
    request_id = create_resp.json()["request_id"]

    db_path = tmp_path / "registrar.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        UPDATE registrations
        SET status = 'complete',
            webhook_delivered = 1,
            webhook_attempts = 3,
            webhook_last_error = 'timeout',
            webhook_next_retry_at = '2999-01-01 00:00:00',
            webhook_delivered_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (request_id,),
    )
    conn.commit()
    conn.close()

    resp = client.post(
        f"/api/webhook/requeue/{request_id}",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    status_resp = client.get(f"/api/status/{request_id}")
    data = status_resp.json()
    assert data["webhook_delivered"] == 0
    assert data["webhook_attempts"] == 0
    assert data["webhook_last_error"] is None
    assert data["webhook_next_retry_at"] is None
    assert data["webhook_delivered_at"] is None


def test_requeue_webhook_requires_terminal_status(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
    }
    create_resp = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )
    request_id = create_resp.json()["request_id"]

    resp = client.post(
        f"/api/webhook/requeue/{request_id}",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 409


def test_recent_failures_requires_api_key(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    resp = client.get("/api/registrations/failures")
    assert resp.status_code == 403


def test_recent_failures_returns_latest_failed_records(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    # create first request
    payload = {
        "name": "alice",
        "parent": "bitcoins.vrsc",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
    }
    first = client.post(
        "/api/register",
        json=payload,
        headers={"X-API-Key": "test-key"},
    ).json()["request_id"]

    time.sleep(0.01)

    # create second request
    second = client.post(
        "/api/register",
        json={
            "name": "bob",
            "parent": "bitcoins.vrsc",
            "native_coin": "VRSC",
            "primary_raddress": "RbobAddress",
        },
        headers={"X-API-Key": "test-key"},
    ).json()["request_id"]

    db_path = tmp_path / "registrar.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE registrations SET status = 'failed', error_message = 'first failed', updated_at = '2026-01-01 00:00:00' WHERE id = ?",
        (first,),
    )
    conn.execute(
        "UPDATE registrations SET status = 'failed', error_message = 'second failed', updated_at = '2026-01-01 00:01:00' WHERE id = ?",
        (second,),
    )
    conn.commit()
    conn.close()

    resp = client.get(
        "/api/registrations/failures?limit=1",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == second
    assert data["items"][0]["error_message"] == "second failed"
