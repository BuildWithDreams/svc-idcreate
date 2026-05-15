import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import id_create_service
import worker


class _FakeRpcPending:
    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 0}


class _FakeRpcConfirmed:
    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 1}


class _FakeRpcIdentitySuccess:
    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        return "txid-idr-1"


class _FakeRpcIdentityFailure:
    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        raise Exception("registration failed")


class _FakeRpcIdentityTimeout:
    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        raise Exception("connection timeout")


class _FakeRpcIdentityAlreadyExists:
    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        raise Exception("Error with registering identity: registeridentity: Identity already exists. (code -27)")

    def get_currency(self, currency_name_or_id):
        return {"idregistrationfees": 25}


class _FakeRpcIdentityCapture:
    def __init__(self, fee=1.25):
        self.fee = fee
        self.last_identity = None
        self.last_fee_offer = None

    def get_currency(self, currency_name_or_id):
        return {"idregistrationfees": self.fee}

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        self.last_identity = json_identity
        self.last_fee_offer = fee_offer
        return "txid-idr-capture"


class _FakeRpcIdentityCaptureNoCurrencyFee:
    def __init__(self):
        self.last_fee_offer = None

    def get_currency(self, currency_name_or_id):
        raise Exception("currency unavailable")

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        self.last_fee_offer = fee_offer
        return "txid-idr-capture"


class _FakeRpcIdentityCaptureEncodedImportFee:
    def __init__(self):
        self.last_fee_offer = None

    def get_currency(self, currency_name_or_id):
        return {
            "idregistrationfees": 0.75,
            "idimportfees": 0.00000001,
            "currencies": [
                "iJhCezBExJHvtyH3fGhNnt2NhU4Ztkf2yq",
                "iRXKBVTVqEPyHrFsUbUW5ahDZRqCWGMTXd",
            ],
            "currencynames": {
                "iJhCezBExJHvtyH3fGhNnt2NhU4Ztkf2yq": "VRSCTEST",
                "iRXKBVTVqEPyHrFsUbUW5ahDZRqCWGMTXd": "SAILING",
            },
            "lastconfirmedcurrencystate": {
                "reservecurrencies": [
                    {
                        "currencyid": "iJhCezBExJHvtyH3fGhNnt2NhU4Ztkf2yq",
                        "priceinreserve": 0.00019985,
                    },
                    {
                        "currencyid": "iRXKBVTVqEPyHrFsUbUW5ahDZRqCWGMTXd",
                        "priceinreserve": 0.00015995,
                    },
                ]
            },
        }

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        self.last_fee_offer = fee_offer
        return "txid-idr-capture"


class _FakeRpcIdentityCaptureImportFeeOutOfRange:
    def __init__(self):
        self.last_fee_offer = None

    def get_currency(self, currency_name_or_id):
        return {
            "idregistrationfees": 1.25,
            "idimportfees": 0.00000009,
            "currencies": ["iOnlyOneReserve"],
            "lastconfirmedcurrencystate": {
                "reservecurrencies": [
                    {
                        "currencyid": "iOnlyOneReserve",
                        "priceinreserve": 0.1,
                    }
                ]
            },
        }

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        self.last_fee_offer = fee_offer
        return "txid-idr-capture"


class _FakeRpcIdentityInsufficientThenSuccess:
    def __init__(self, fee=100.0, fail_count=1):
        self.fee = fee
        self.fail_count = fail_count
        self.calls = 0
        self.fee_offers = []

    def get_currency(self, currency_name_or_id):
        return {"idregistrationfees": self.fee}

    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        self.calls += 1
        self.fee_offers.append(fee_offer)
        if self.calls <= self.fail_count:
            raise Exception("registeridentity: Insufficient funds for identity registration (code -8)")
        return "txid-idr-capture"


class _FakeRpcIdrPending:
    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 0}


class _FakeRpcIdrConfirmed:
    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 2}


class _FakeRpcPendingTimeout:
    def get_raw_transaction(self, txid, verbose=1):
        raise Exception("pending rpc timeout")


class _FakeRpcSubmittedTimeout:
    def get_raw_transaction(self, txid, verbose=1):
        raise Exception("submitted rpc timeout")


def _seed_registration(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-1",
            "alice",
            "bitcoins.vrsc",
            "VRSC",
            "verusd_vrsc",
            "Ralice",
            "Rfunds",
            "Rfunds",
            "pending_rnc_confirm",
            "txid-rnc-1",
            '{"txid":"txid-rnc-1","namereservation":{"name":"alice","salt":"abc"}}',
        ),
    )
    conn.commit()
    conn.close()

    return db_path


def _seed_pending_with_retry(monkeypatch, tmp_path, attempts=0, next_retry_at=None):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json,
            attempts,
            next_retry_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-pending-retry",
            "alice",
            "bitcoins.vrsc",
            "VRSC",
            "verusd_vrsc",
            "Ralice",
            "Rfunds",
            "Rfunds",
            "pending_rnc_confirm",
            "txid-rnc-1",
            '{"txid":"txid-rnc-1","namereservation":{"name":"alice","salt":"abc"}}',
            attempts,
            next_retry_at,
        ),
    )
    conn.commit()
    conn.close()

    return db_path


def _seed_ready_for_idr(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-2",
            "alice",
            "bitcoins.vrsc",
            "VRSC",
            "verusd_vrsc",
            "Ralice",
            "Rfunds",
            "Rfunds",
            "ready_for_idr",
            "txid-rnc-1",
            '{"txid":"txid-rnc-1","namereservation":{"name":"alice","salt":"abc"}}',
        ),
    )
    conn.commit()
    conn.close()

    return db_path


def _seed_ready_for_idr_with_retry(monkeypatch, tmp_path, attempts=0, next_retry_at=None):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json,
            attempts,
            next_retry_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-retry",
            "alice",
            "bitcoins.vrsc",
            "VRSC",
            "verusd_vrsc",
            "Ralice",
            "Rfunds",
            "Rfunds",
            "ready_for_idr",
            "txid-rnc-1",
            '{"txid":"txid-rnc-1","namereservation":{"name":"alice","salt":"abc"}}',
            attempts,
            next_retry_at,
        ),
    )
    conn.commit()
    conn.close()

    return db_path


def _seed_idr_submitted(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json,
            idr_txid
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-3",
            "alice",
            "bitcoins.vrsc",
            "VRSC",
            "verusd_vrsc",
            "Ralice",
            "Rfunds",
            "Rfunds",
            "idr_submitted",
            "txid-rnc-1",
            '{"txid":"txid-rnc-1","namereservation":{"name":"alice","salt":"abc"}}',
            "txid-idr-1",
        ),
    )
    conn.commit()
    conn.close()

    return db_path


def _seed_idr_submitted_with_retry(monkeypatch, tmp_path, attempts=0, next_retry_at=None):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json,
            idr_txid,
            attempts,
            next_retry_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-submitted-retry",
            "alice",
            "bitcoins.vrsc",
            "VRSC",
            "verusd_vrsc",
            "Ralice",
            "Rfunds",
            "Rfunds",
            "idr_submitted",
            "txid-rnc-1",
            '{"txid":"txid-rnc-1","namereservation":{"name":"alice","salt":"abc"}}',
            "txid-idr-1",
            attempts,
            next_retry_at,
        ),
    )
    conn.commit()
    conn.close()

    return db_path


def _seed_complete_with_webhook(
    monkeypatch,
    tmp_path,
    webhook_attempts=0,
    webhook_next_retry_at=None,
):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            control_address,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json,
            idr_txid,
            webhook_url,
            webhook_secret,
            webhook_delivered,
            webhook_attempts,
            webhook_next_retry_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-webhook-complete",
            "alice",
            "bitcoins.vrsc",
            "VRSC",
            "verusd_vrsc",
            "Ralice",
            "Rfunds",
            "Rfunds",
            "complete",
            "txid-rnc-1",
            '{"txid":"txid-rnc-1","namereservation":{"name":"alice","salt":"abc"}}',
            "txid-idr-1",
            "https://example.com/hook",
            "supersecret",
            0,
            webhook_attempts,
            webhook_next_retry_at,
        ),
    )
    conn.commit()
    conn.close()

    return db_path


def test_worker_keeps_pending_when_not_confirmed(monkeypatch, tmp_path):
    _seed_registration(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcPending())

    updated = worker.process_once()

    assert updated == 0
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute("SELECT status FROM registrations WHERE id = ?", ("req-1",)).fetchone()
    conn.close()
    assert row[0] == "pending_rnc_confirm"


def test_worker_advances_to_ready_for_idr_when_confirmed(monkeypatch, tmp_path):
    _seed_registration(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcConfirmed())

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, updated_at FROM registrations WHERE id = ?", ("req-1",)
    ).fetchone()
    conn.close()
    assert row[0] == "ready_for_idr"
    assert row[1] is not None


def test_worker_submits_id_registration_when_ready(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdentitySuccess())

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, idr_txid FROM registrations WHERE id = ?", ("req-2",)
    ).fetchone()
    conn.close()
    assert row[0] == "idr_submitted"
    assert row[1] == "txid-idr-1"


def test_worker_marks_failed_when_id_registration_errors(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdentityFailure())
    monkeypatch.setenv("WORKER_MAX_RETRIES", "1")

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, error_message FROM registrations WHERE id = ?", ("req-2",)
    ).fetchone()
    conn.close()
    assert row[0] == "failed"
    assert "registration failed" in row[1]


def test_worker_keeps_idr_submitted_when_not_confirmed(monkeypatch, tmp_path):
    _seed_idr_submitted(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdrPending())

    updated = worker.process_once()

    assert updated == 0
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute("SELECT status FROM registrations WHERE id = ?", ("req-3",)).fetchone()
    conn.close()
    assert row[0] == "idr_submitted"


def test_worker_marks_complete_when_idr_confirmed(monkeypatch, tmp_path):
    _seed_idr_submitted(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdrConfirmed())

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, updated_at FROM registrations WHERE id = ?", ("req-3",)
    ).fetchone()
    conn.close()
    assert row[0] == "complete"
    assert row[1] is not None


def test_worker_schedules_retry_on_transient_id_registration_error(monkeypatch, tmp_path):
    _seed_ready_for_idr_with_retry(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdentityTimeout())
    monkeypatch.setenv("WORKER_MAX_RETRIES", "3")
    monkeypatch.setenv("WORKER_RETRY_BASE_SECONDS", "5")

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts, error_message, next_retry_at FROM registrations WHERE id = ?",
        ("req-retry",),
    ).fetchone()
    conn.close()
    assert row[0] == "ready_for_idr"
    assert row[1] == 1
    assert "connection timeout" in row[2]
    assert row[3] is not None


def test_worker_marks_complete_when_id_already_exists_on_ready_for_idr(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdentityAlreadyExists())

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts, next_retry_at, error_message FROM registrations WHERE id = ?",
        ("req-2",),
    ).fetchone()
    conn.close()
    assert row[0] == "complete"
    assert row[1] == 0
    assert row[2] is None
    assert row[3] is None


def test_worker_marks_failed_after_max_retry_attempts(monkeypatch, tmp_path):
    _seed_ready_for_idr_with_retry(monkeypatch, tmp_path, attempts=2)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdentityTimeout())
    monkeypatch.setenv("WORKER_MAX_RETRIES", "3")
    monkeypatch.setenv("WORKER_RETRY_BASE_SECONDS", "5")

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts, error_message, next_retry_at FROM registrations WHERE id = ?",
        ("req-retry",),
    ).fetchone()
    conn.close()
    assert row[0] == "failed"
    assert row[1] == 3
    assert "connection timeout" in row[2]
    assert row[3] is None


def test_worker_skips_ready_for_idr_until_retry_window(monkeypatch, tmp_path):
    _seed_ready_for_idr_with_retry(
        monkeypatch,
        tmp_path,
        attempts=1,
        next_retry_at="2999-01-01 00:00:00",
    )
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdentitySuccess())

    updated = worker.process_once()

    assert updated == 0
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts, idr_txid FROM registrations WHERE id = ?",
        ("req-retry",),
    ).fetchone()
    conn.close()
    assert row[0] == "ready_for_idr"
    assert row[1] == 1
    assert row[2] is None


def test_worker_retries_pending_on_rpc_error(monkeypatch, tmp_path):
    _seed_pending_with_retry(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcPendingTimeout())
    monkeypatch.setenv("WORKER_MAX_RETRIES", "3")
    monkeypatch.setenv("WORKER_RETRY_BASE_SECONDS", "5")

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts, error_message, next_retry_at FROM registrations WHERE id = ?",
        ("req-pending-retry",),
    ).fetchone()
    conn.close()
    assert row[0] == "pending_rnc_confirm"
    assert row[1] == 1
    assert "pending rpc timeout" in row[2]
    assert row[3] is not None


def test_worker_retries_submitted_on_rpc_error(monkeypatch, tmp_path):
    _seed_idr_submitted_with_retry(monkeypatch, tmp_path)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcSubmittedTimeout())
    monkeypatch.setenv("WORKER_MAX_RETRIES", "3")
    monkeypatch.setenv("WORKER_RETRY_BASE_SECONDS", "5")

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts, error_message, next_retry_at FROM registrations WHERE id = ?",
        ("req-submitted-retry",),
    ).fetchone()
    conn.close()
    assert row[0] == "idr_submitted"
    assert row[1] == 1
    assert "submitted rpc timeout" in row[2]
    assert row[3] is not None


def test_worker_skips_pending_until_retry_window(monkeypatch, tmp_path):
    _seed_pending_with_retry(
        monkeypatch,
        tmp_path,
        attempts=1,
        next_retry_at="2999-01-01 00:00:00",
    )
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcConfirmed())

    updated = worker.process_once()

    assert updated == 0
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts FROM registrations WHERE id = ?",
        ("req-pending-retry",),
    ).fetchone()
    conn.close()
    assert row[0] == "pending_rnc_confirm"
    assert row[1] == 1


def test_worker_skips_submitted_until_retry_window(monkeypatch, tmp_path):
    _seed_idr_submitted_with_retry(
        monkeypatch,
        tmp_path,
        attempts=1,
        next_retry_at="2999-01-01 00:00:00",
    )
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: _FakeRpcIdrConfirmed())

    updated = worker.process_once()

    assert updated == 0
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts FROM registrations WHERE id = ?",
        ("req-submitted-retry",),
    ).fetchone()
    conn.close()
    assert row[0] == "idr_submitted"
    assert row[1] == 1


def test_worker_delivers_webhook_for_complete_status(monkeypatch, tmp_path):
    _seed_complete_with_webhook(monkeypatch, tmp_path)
    captured = {}

    def _fake_post(url, payload, headers, timeout_seconds):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout"] = timeout_seconds

    monkeypatch.setattr(worker, "_post_webhook", _fake_post)

    updated = worker.process_once()

    assert updated == 1
    assert captured["url"] == "https://example.com/hook"
    assert captured["payload"]["event"] == "registration.complete"
    assert captured["payload"]["request_id"] == "req-webhook-complete"
    assert "X-Webhook-Signature" in captured["headers"]

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT webhook_delivered, webhook_attempts, webhook_last_error, webhook_delivered_at FROM registrations WHERE id = ?",
        ("req-webhook-complete",),
    ).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == 1
    assert row[2] is None
    assert row[3] is not None


def test_worker_retries_webhook_delivery_on_error(monkeypatch, tmp_path):
    _seed_complete_with_webhook(monkeypatch, tmp_path)

    def _fake_post(url, payload, headers, timeout_seconds):
        raise Exception("webhook timeout")

    monkeypatch.setattr(worker, "_post_webhook", _fake_post)
    monkeypatch.setenv("WEBHOOK_MAX_RETRIES", "3")
    monkeypatch.setenv("WEBHOOK_RETRY_BASE_SECONDS", "10")

    updated = worker.process_once()

    assert updated == 1
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT webhook_delivered, webhook_attempts, webhook_last_error, webhook_next_retry_at FROM registrations WHERE id = ?",
        ("req-webhook-complete",),
    ).fetchone()
    conn.close()
    assert row[0] == 0
    assert row[1] == 1
    assert "webhook timeout" in row[2]
    assert row[3] is not None


def test_worker_skips_webhook_until_retry_window(monkeypatch, tmp_path):
    _seed_complete_with_webhook(
        monkeypatch,
        tmp_path,
        webhook_attempts=1,
        webhook_next_retry_at="2999-01-01 00:00:00",
    )

    def _fake_post(url, payload, headers, timeout_seconds):
        raise Exception("should not be called")

    monkeypatch.setattr(worker, "_post_webhook", _fake_post)

    updated = worker.process_once()

    assert updated == 0
    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT webhook_delivered, webhook_attempts FROM registrations WHERE id = ?",
        ("req-webhook-complete",),
    ).fetchone()
    conn.close()
    assert row[0] == 0
    assert row[1] == 1


def test_worker_process_once_includes_storage_sweep_count(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    id_create_service._init_db()

    called = {"count": 0}

    def _fake_storage_once():
        called["count"] += 1
        return 2

    monkeypatch.setattr(worker, "process_storage_once", _fake_storage_once)

    updated = worker.process_once()

    assert called["count"] == 1
    assert updated == 2


def test_worker_builds_identity_payload_with_minimumsignature(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    rpc = _FakeRpcIdentityCapture(fee=1.25)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: rpc)
    monkeypatch.setenv("MINIMUM_SIGNATURES", "2")
    monkeypatch.setenv("Z_ADDRESS", "")
    monkeypatch.delenv("FEE_OFFER", raising=False)

    updated = worker.process_once()

    assert updated == 1
    assert rpc.last_identity is not None
    assert rpc.last_identity["name"] == "alice.bitcoins.vrsc"
    assert rpc.last_identity["primaryaddresses"] == ["Ralice"]
    assert rpc.last_identity["privateaddresses"] == ""
    assert rpc.last_identity["minimumsignature"] == 2
    assert "minimumsignatures" not in rpc.last_identity
    assert rpc.last_fee_offer == 1.25


def test_worker_fee_offer_env_overrides_currency_fee(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    rpc = _FakeRpcIdentityCaptureNoCurrencyFee()
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: rpc)
    monkeypatch.setenv("FEE_OFFER", "3")

    updated = worker.process_once()

    assert updated == 1
    assert rpc.last_fee_offer == 3.0


def test_worker_fee_offer_uses_encoded_import_fee_index(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    rpc = _FakeRpcIdentityCaptureEncodedImportFee()
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: rpc)
    monkeypatch.delenv("FEE_OFFER", raising=False)

    updated = worker.process_once()

    assert updated == 1
    # 0.75 / 0.00015995 = 4688.965301656767...
    assert rpc.last_fee_offer == pytest.approx(4688.96530166, rel=0, abs=1e-8)


def test_worker_fee_offer_falls_back_when_encoded_index_out_of_range(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    rpc = _FakeRpcIdentityCaptureImportFeeOutOfRange()
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: rpc)
    monkeypatch.delenv("FEE_OFFER", raising=False)

    updated = worker.process_once()

    assert updated == 1
    assert rpc.last_fee_offer == 1.25


def test_worker_ready_for_idr_retries_with_fee_bump_on_insufficient_funds(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    rpc = _FakeRpcIdentityInsufficientThenSuccess(fee=100.0, fail_count=1)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: rpc)
    monkeypatch.setenv("IDR_FEE_BUMP_RATE", "0.0025")
    monkeypatch.setenv("IDR_FEE_BUMP_MAX_INCREASES", "5")
    monkeypatch.delenv("FEE_OFFER", raising=False)

    updated = worker.process_once()

    assert updated == 1
    assert len(rpc.fee_offers) == 2
    assert rpc.fee_offers[0] == 100.0
    assert rpc.fee_offers[1] == pytest.approx(100.25, rel=0, abs=1e-8)

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, idr_txid, attempts FROM registrations WHERE id = ?",
        ("req-2",),
    ).fetchone()
    conn.close()
    assert row[0] == "idr_submitted"
    assert row[1] == "txid-idr-capture"
    assert row[2] == 0


def test_worker_ready_for_idr_stops_after_max_fee_bumps(monkeypatch, tmp_path):
    _seed_ready_for_idr(monkeypatch, tmp_path)
    rpc = _FakeRpcIdentityInsufficientThenSuccess(fee=100.0, fail_count=99)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda _: rpc)
    monkeypatch.setenv("IDR_FEE_BUMP_RATE", "0.0025")
    monkeypatch.setenv("IDR_FEE_BUMP_MAX_INCREASES", "2")
    monkeypatch.delenv("FEE_OFFER", raising=False)

    updated = worker.process_once()

    assert updated == 1
    # base + 2 fee-bumped retries
    assert len(rpc.fee_offers) == 3
    assert rpc.fee_offers[0] == 100.0
    assert rpc.fee_offers[1] == pytest.approx(100.25, rel=0, abs=1e-8)
    assert rpc.fee_offers[2] == pytest.approx(100.500625, rel=0, abs=1e-8)

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    row = conn.execute(
        "SELECT status, attempts, next_retry_at, error_message FROM registrations WHERE id = ?",
        ("req-2",),
    ).fetchone()
    conn.close()
    assert row[0] == "ready_for_idr"
    assert row[1] == 1
    assert row[2] is not None
    assert "Insufficient funds for identity registration" in row[3]
