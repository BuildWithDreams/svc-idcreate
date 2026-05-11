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


class _FakeFractionalExistingReservesRpc:
    def __init__(self):
        self.sent_calls = []
        self.balances = {
            "RtestAddress": {
                "VRSCTEST": 1000.0,
                "SPORTS": 10.0,
                "SAILING": 10.0,
                "YEN": 10.0,
            },
            "DPNK@": {},
            "blockoneminer@": {},
        }

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

    def get_currency_balance(self, holder):
        return dict(self.balances.get(holder, {}))

    def send_currency_simple_to_identity(self, from_address, currency, identity, amount):
        self.sent_calls.append((from_address, currency, identity, amount))
        source_balances = self.balances.setdefault(from_address, {})
        source_balances[currency] = float(source_balances.get(currency, 0.0)) - float(amount)
        target_balances = self.balances.setdefault(identity, {})
        target_balances[currency] = float(target_balances.get(currency, 0.0)) + float(amount)
        return "c" * 64

    def define_currency(self, options):
        return "d" * 64


class _FakeFractionalReserveIdentityExistsRpc:
    def __init__(self):
        self.sent_calls = []
        self.reserve_rnc_calls = []
        self.reserve_register_calls = []
        self.balances = {
            "RtestAddress": {
                "VRSCTEST": 1000.0,
            },
            "DPNK@": {},
            "SPORTS@": {},
            "blockoneminer@": {},
        }
        self.currency_supply = {}

    def register_name_commitment(self, name, primary_raddress, referral_id, parent, source_of_funds):
        if name == "SPORTS":
            self.reserve_rnc_calls.append((name, parent))
        return {
            "txid": "a" * 64,
            "namereservation": {"name": name, "salt": "abc123"},
        }

    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 1}

    def get_currency(self, currency_name_or_id):
        supply = self.currency_supply.get(currency_name_or_id)
        if supply is None:
            return {"idregistrationfees": 25}
        return {
            "idregistrationfees": 25,
            "lastconfirmedcurrencystate": {"supply": supply},
        }

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        name = json_identity.get("name", "")
        if name.startswith("SPORTS."):
            self.reserve_register_calls.append(name)
        return "b" * 64

    def get_currency_balance(self, holder):
        return dict(self.balances.get(holder, {}))

    def send_currency_simple_to_identity(self, from_address, currency, identity, amount):
        self.sent_calls.append((from_address, currency, identity, amount))
        source_balances = self.balances.setdefault(from_address, {})
        source_balances[currency] = float(source_balances.get(currency, 0.0)) - float(amount)
        target_balances = self.balances.setdefault(identity, {})
        target_balances[currency] = float(target_balances.get(currency, 0.0)) + float(amount)
        return "c" * 64

    def define_simple_token_currency(self, options, name, id_registration_fees, pre_allocations, proof_protocol):
        if pre_allocations and isinstance(pre_allocations[0], dict):
            for alloc_identity, amount in pre_allocations[0].items():
                alloc_balances = self.balances.setdefault(alloc_identity, {})
                alloc_balances[name] = float(alloc_balances.get(name, 0.0)) + float(amount)
                self.currency_supply[name] = float(amount)
        return "d" * 64

    def define_currency(self, options):
        return "e" * 64


class _FakeFractionalIdentityExistsRpc:
    def __init__(self):
        self.sent_calls = []
        self.fractional_rnc_calls = []
        self.fractional_register_calls = []
        self.balances = {
            "RtestAddress": {
                "VRSCTEST": 1000.0,
                "SPORTS": 10.0,
            },
            "DPNK@": {},
        }

    def register_name_commitment(self, name, primary_raddress, referral_id, parent, source_of_funds):
        if name == "DPNK":
            self.fractional_rnc_calls.append((name, parent))
        return {
            "txid": "a" * 64,
            "namereservation": {"name": name, "salt": "abc123"},
        }

    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 1}

    def get_currency(self, currency_name_or_id):
        return {"idregistrationfees": 25}

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        identity_name = json_identity.get("name", "")
        if identity_name.startswith("DPNK."):
            self.fractional_register_calls.append(identity_name)
        return "b" * 64

    def get_currency_balance(self, holder):
        return dict(self.balances.get(holder, {}))

    def send_currency_simple_to_identity(self, from_address, currency, identity, amount):
        self.sent_calls.append((from_address, currency, identity, amount))
        source_balances = self.balances.setdefault(from_address, {})
        source_balances[currency] = float(source_balances.get(currency, 0.0)) - float(amount)
        target_balances = self.balances.setdefault(identity, {})
        target_balances[currency] = float(target_balances.get(currency, 0.0)) + float(amount)
        return "c" * 64

    def define_currency(self, options):
        return "d" * 64


class _FakeFractionalDustShortfallRpc:
    def __init__(self):
        self.sent_calls = []
        self.balances = {
            "RtestAddress": {
                "VRSCTEST": 1000.0,
            },
            # Slightly below requested 20.0 to exercise dust shortfall handling.
            "DPNK@": {
                "VRSCTEST": 19.999999999,
            },
        }

    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 1}

    def get_currency(self, currency_name_or_id):
        return {"idregistrationfees": 25}

    def get_currency_balance(self, holder):
        return dict(self.balances.get(holder, {}))

    def send_currency_simple_to_identity(self, from_address, currency, identity, amount):
        self.sent_calls.append((from_address, currency, identity, amount))
        source_balances = self.balances.setdefault(from_address, {})
        source_balances[currency] = float(source_balances.get(currency, 0.0)) - float(amount)
        target_balances = self.balances.setdefault(identity, {})
        target_balances[currency] = float(target_balances.get(currency, 0.0)) + float(amount)
        return "c" * 64

    def define_currency(self, options):
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


def test_worker_fractional_uses_primary_raddress_for_existing_reserve_contributions(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")
    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: "verusd_vrsc")

    with TestClient(id_create_service.app) as client:
        resp = client.post(
            "/api/currency/plan",
            json={
                "name": "DPNK",
                "parent": "VRSCTEST",
                "native_coin": "VRSCTEST",
                "primary_raddress": "RtestAddress",
                "mode": "fractional",
                "fractional": {
                    "initial_supply": 325000,
                    "id_registration_fees": 777,
                    "id_referral_levels": 3,
                    "start_block": 1057000,
                    "native": {
                        "name": "VRSCTEST",
                        "weight": 0.55,
                        "initial_contribution": 20,
                    },
                    "reserves": [
                        {
                            "name": "SPORTS",
                            "weight": 0.2,
                            "initial_contribution": 0.1,
                        },
                        {
                            "name": "SAILING",
                            "weight": 0.2,
                            "initial_contribution": 0.1,
                        },
                        {
                            "name": "YEN",
                            "weight": 0.05,
                            "initial_contribution": 0.1,
                        },
                    ],
                    "define_funding_amount": 200.001,
                    "create_reserves": False,
                    "prepare_fractional_identity": True,
                },
            },
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    fake_rpc = _FakeFractionalExistingReservesRpc()
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: fake_rpc)

    for _ in range(25):
        worker.process_once()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, step_index FROM currency_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "complete"
    assert row["step_index"] == 6

    reserve_calls = [call for call in fake_rpc.sent_calls if call[1] in {"SPORTS", "SAILING", "YEN"}]
    assert len(reserve_calls) == 3
    assert all(call[0] == "RtestAddress" for call in reserve_calls)
    assert all(call[2] == "DPNK@" for call in reserve_calls)


def test_worker_fractional_skips_reserve_identity_creation_when_identity_exists(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")
    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: "verusd_vrsc")

    with TestClient(id_create_service.app) as client:
        resp = client.post(
            "/api/currency/plan",
            json={
                "name": "DPNK",
                "parent": "VRSCTEST",
                "native_coin": "VRSCTEST",
                "primary_raddress": "RtestAddress",
                "mode": "fractional",
                "fractional": {
                    "initial_supply": 325000,
                    "id_registration_fees": 777,
                    "id_referral_levels": 3,
                    "start_block": 1057000,
                    "native": {
                        "name": "VRSCTEST",
                        "weight": 0.55,
                        "initial_contribution": 20,
                    },
                    "reserves": [
                        {
                            "name": "SPORTS",
                            "supply": 80000,
                            "identity_exists": True,
                            "weight": 0.2,
                            "initial_contribution": 0.1,
                        }
                    ],
                    "allocation_id": "blockoneminer@",
                    "define_funding_amount": 200.001,
                    "create_reserves": True,
                    "prepare_fractional_identity": True,
                },
            },
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    fake_rpc = _FakeFractionalReserveIdentityExistsRpc()
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: fake_rpc)

    for _ in range(30):
        worker.process_once()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, step_index FROM currency_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "complete"
    assert row["step_index"] == 6
    assert fake_rpc.reserve_rnc_calls == []
    assert fake_rpc.reserve_register_calls == []

    reserve_contribution_calls = [call for call in fake_rpc.sent_calls if call[1] == "SPORTS" and call[2] == "DPNK@"]
    assert reserve_contribution_calls
    assert all(call[0] == "blockoneminer@" for call in reserve_contribution_calls)


def test_worker_fractional_skips_main_identity_creation_when_identity_exists(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")
    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: "verusd_vrsc")

    with TestClient(id_create_service.app) as client:
        resp = client.post(
            "/api/currency/plan",
            json={
                "name": "DPNK",
                "parent": "VRSCTEST",
                "native_coin": "VRSCTEST",
                "primary_raddress": "RtestAddress",
                "mode": "fractional",
                "fractional": {
                    "initial_supply": 325000,
                    "id_registration_fees": 777,
                    "id_referral_levels": 3,
                    "start_block": 1057000,
                    "native": {
                        "name": "VRSCTEST",
                        "weight": 0.55,
                        "initial_contribution": 20,
                    },
                    "reserves": [
                        {
                            "name": "SPORTS",
                            "weight": 0.2,
                            "initial_contribution": 0.1,
                        }
                    ],
                    "define_funding_amount": 200.001,
                    "create_reserves": False,
                    "prepare_fractional_identity": True,
                    "identity_exists": True,
                },
            },
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    fake_rpc = _FakeFractionalIdentityExistsRpc()
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: fake_rpc)

    for _ in range(30):
        worker.process_once()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, step_index FROM currency_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "complete"
    assert row["step_index"] == 6
    assert fake_rpc.fractional_rnc_calls == []
    assert fake_rpc.fractional_register_calls == []

    funding_calls = [call for call in fake_rpc.sent_calls if call[1] == "VRSCTEST" and call[2] == "DPNK@"]
    assert funding_calls
    assert all(call[0] == "RtestAddress" for call in funding_calls)

    reserve_contribution_calls = [call for call in fake_rpc.sent_calls if call[1] == "SPORTS" and call[2] == "DPNK@"]
    assert reserve_contribution_calls
    assert all(call[0] == "RtestAddress" for call in reserve_contribution_calls)


def test_worker_fractional_native_dust_shortfall_does_not_submit_send(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("SOURCE_OF_FUNDS", "RsourceFundsAddr")
    monkeypatch.setattr(id_create_service, "_resolve_daemon_by_native_coin", lambda _: "verusd_vrsc")

    with TestClient(id_create_service.app) as client:
        resp = client.post(
            "/api/currency/plan",
            json={
                "name": "DPNK",
                "parent": "VRSCTEST",
                "native_coin": "VRSCTEST",
                "primary_raddress": "RtestAddress",
                "mode": "fractional",
                "fractional": {
                    "initial_supply": 325000,
                    "id_registration_fees": 777,
                    "id_referral_levels": 3,
                    "start_block": 1057000,
                    "native": {
                        "name": "VRSCTEST",
                        "weight": 1.0,
                        "initial_contribution": 20,
                    },
                    "reserves": [],
                    "define_funding_amount": 200.001,
                    "create_reserves": False,
                    "prepare_fractional_identity": False,
                },
            },
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    fake_rpc = _FakeFractionalDustShortfallRpc()
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: fake_rpc)

    for _ in range(20):
        worker.process_once()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, step_index FROM currency_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "complete"
    assert row["step_index"] == 6
    assert fake_rpc.sent_calls == []
