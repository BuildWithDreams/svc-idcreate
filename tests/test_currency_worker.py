import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import id_create_service
import worker


class _FakeCurrencyRpc:
    def register_name_commitment(self, name, primary_raddress, referral_id, parent, source_of_funds):
        return {
            "txid": "a" * 64,
            "namereservation": {"name": name, "salt": "abc123"},
        }

    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 1}

    def get_currency(self, currency_name_or_id):
        return {"idregistrationfees": 25}

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        return "b" * 64

    def send_currency_simple_to_identity(self, from_address, currency, identity, amount):
        return "opid-1"

    def z_get_operation_status(self, opid):
        return [{"result": {"txid": "c" * 64}}]

    def define_simple_token_currency(self, options, name, id_registration_fees, pre_allocations, proof_protocol):
        return "d" * 64


def test_worker_advances_simple_currency_to_complete(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")
    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: "verusd_vrsc")

    with TestClient(id_create_service.app) as client:
        resp = client.post(
            "/api/currency/simple",
            json={
                "name": "TENNIS",
                "parent": "bitcoins.vrsc",
                "native_coin": "VRSC",
                "primary_raddress": "RtestAddress",
                "pre_allocation_id": "blockoneminer@",
                "pre_allocation_amount": 80000,
            },
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeCurrencyRpc())

    for _ in range(10):
        worker.process_once()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, step_index FROM currency_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "complete"
    assert row["step_index"] == 4
