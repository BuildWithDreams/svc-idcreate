import sqlite3
import json
import os
import hmac
import hashlib
import logging
import re
from urllib import request as urllib_request
from typing import Any

import id_create_service


logger = logging.getLogger(__name__)


def _to_log_dict(row: sqlite3.Row | dict[str, Any] | None, redacted_keys: set[str] | None = None) -> dict[str, Any]:
    if row is None:
        return {}

    if isinstance(row, sqlite3.Row):
        data = dict(row)
    elif isinstance(row, dict):
        data = dict(row)
    else:
        data = {"value": str(row)}

    if redacted_keys:
        for key in redacted_keys:
            if key in data and data[key] is not None:
                data[key] = "***REDACTED***"

    return data


def _log_json(data: Any) -> str:
    try:
        return json.dumps(data, sort_keys=True, default=str)
    except Exception:
        return str(data)


def _get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(id_create_service._get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _get_rpc_connection(daemon_name: str) -> Any:
    return id_create_service._get_rpc_connection(daemon_name)


def _retry_config() -> tuple[int, int]:
    max_retries = int(os.getenv("WORKER_MAX_RETRIES", "5"))
    base_seconds = int(os.getenv("WORKER_RETRY_BASE_SECONDS", "15"))
    return max_retries, base_seconds


def _record_retry_or_failure(conn: sqlite3.Connection, row_id: str, attempts: int, error: str, status: str):
    max_retries, base_seconds = _retry_config()
    next_attempt = attempts + 1

    if next_attempt >= max_retries:
        conn.execute(
            """
            UPDATE registrations
            SET status = ?, attempts = ?, error_message = ?, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("failed", next_attempt, error, row_id),
        )
        return

    delay_seconds = base_seconds * (2 ** (next_attempt - 1))
    conn.execute(
        """
        UPDATE registrations
        SET status = ?, attempts = ?, error_message = ?, next_retry_at = datetime('now', ?), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, next_attempt, error, f"+{delay_seconds} seconds", row_id),
    )


def _record_currency_retry_or_failure(conn: sqlite3.Connection, row_id: str, attempts: int, error: str, status: str):
    max_retries, base_seconds = _retry_config()
    next_attempt = attempts + 1

    if next_attempt >= max_retries:
        conn.execute(
            """
            UPDATE currency_requests
            SET status = 'failed', attempts = ?, error_message = ?, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_attempt, error, row_id),
        )
        return

    delay_seconds = base_seconds * (2 ** (next_attempt - 1))
    conn.execute(
        """
        UPDATE currency_requests
        SET status = ?, attempts = ?, error_message = ?, next_retry_at = datetime('now', ?), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, next_attempt, error, f"+{delay_seconds} seconds", row_id),
    )


def _webhook_retry_config() -> tuple[int, int]:
    max_retries = int(os.getenv("WEBHOOK_MAX_RETRIES", "5"))
    base_seconds = int(os.getenv("WEBHOOK_RETRY_BASE_SECONDS", "15"))
    return max_retries, base_seconds


def _record_webhook_retry_or_failure(conn: sqlite3.Connection, row_id: str, attempts: int, error: str):
    max_retries, base_seconds = _webhook_retry_config()
    next_attempt = attempts + 1

    if next_attempt >= max_retries:
        conn.execute(
            """
            UPDATE registrations
            SET webhook_attempts = ?, webhook_last_error = ?, webhook_next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_attempt, error, row_id),
        )
        return

    delay_seconds = base_seconds * (2 ** (next_attempt - 1))
    conn.execute(
        """
        UPDATE registrations
        SET webhook_attempts = ?, webhook_last_error = ?, webhook_next_retry_at = datetime('now', ?), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (next_attempt, error, f"+{delay_seconds} seconds", row_id),
    )


def _webhook_signature(secret: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post_webhook(url: str, payload: dict, headers: dict, timeout_seconds: int):
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    req = urllib_request.Request(url=url, method="POST", data=body)
    for key, value in headers.items():
        req.add_header(key, value)

    with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
        status_code = getattr(response, "status", 200)
        if status_code >= 400:
            raise Exception(f"Webhook delivery failed with status {status_code}")


def _is_permanent_storage_error(error_message: str) -> bool:
    lowered = error_message.lower()
    return "bad-txns-failed-precheck" in lowered or "validation" in lowered


def _build_identity_payload(full_name: str, primary_raddress: str) -> dict:
    private_address = os.getenv("Z_ADDRESS", "").strip()
    minimum_signature = int(os.getenv("MINIMUM_SIGNATURES", "1"))
    if minimum_signature < 1:
        minimum_signature = 1

    return {
        "name": full_name,
        "primaryaddresses": [primary_raddress],
        "privateaddresses": private_address,
        "minimumsignature": minimum_signature,
    }


def _resolve_fee_offer(rpc: Any, parent_namespace: str) -> float | int:
    fee_offer_env = os.getenv("FEE_OFFER", "").strip()
    if fee_offer_env:
        try:
            return float(fee_offer_env)
        except ValueError:
            logger.warning("Invalid FEE_OFFER value=%s; falling back to currency idregistrationfees", fee_offer_env)

    try:
        currency = rpc.get_currency(parent_namespace)
        if isinstance(currency, dict) and currency.get("idregistrationfees") is not None:
            return currency["idregistrationfees"]
    except Exception as exc:
        logger.warning("Failed to resolve idregistrationfees for parent=%s error=%s", parent_namespace, exc)

    # Preserve historical behavior when dynamic fee lookup is unavailable.
    return 1


def _parse_json_or_empty(raw_json: str | None) -> dict:
    if not raw_json:
        return {}
    try:
        loaded = json.loads(raw_json)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _extract_operation_or_txid(result: Any) -> tuple[str, str]:
    """Normalize sendcurrency/related response into (wait_type, value)."""
    if isinstance(result, str):
        if result.startswith("opid-"):
            return "opid_txid", result
        if re.fullmatch(r"[0-9a-fA-F]{64}", result):
            return "tx_confirm", result
        return "opid_txid", result

    if isinstance(result, dict):
        if isinstance(result.get("opid"), str):
            return "opid_txid", result["opid"]
        if isinstance(result.get("txid"), str):
            return "tx_confirm", result["txid"]
        if isinstance(result.get("result"), str):
            value = result["result"]
            if value.startswith("opid-"):
                return "opid_txid", value
            if re.fullmatch(r"[0-9a-fA-F]{64}", value):
                return "tx_confirm", value

    raise Exception(f"Unexpected operation response shape: {result}")


def _save_currency_state(
    conn: sqlite3.Connection,
    row_id: str,
    *,
    status: str,
    step_index: int,
    progress: dict,
    wait_type: str | None = None,
    wait_value: str | None = None,
    error_message: str | None = None,
):
    conn.execute(
        """
        UPDATE currency_requests
        SET status = ?,
            step_index = ?,
            progress_json = ?,
            wait_type = ?,
            wait_value = ?,
            attempts = 0,
            next_retry_at = NULL,
            error_message = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            status,
            step_index,
            json.dumps(progress),
            wait_type,
            wait_value,
            error_message,
            row_id,
        ),
    )


def _poll_operation_for_txid(rpc: Any, opid: str) -> str | None:
    statuses = rpc.z_get_operation_status(opid)
    if not isinstance(statuses, list) or not statuses:
        return None

    first = statuses[0]
    if not isinstance(first, dict):
        return None

    result = first.get("result")
    if isinstance(result, dict) and isinstance(result.get("txid"), str):
        return result["txid"]
    return None


def _get_currency_balance_amount(rpc: Any, holder: str, currency: str) -> float:
    balances = rpc.get_currency_balance(holder)
    if not isinstance(balances, dict):
        return 0.0
    try:
        return float(balances.get(currency, 0.0))
    except Exception:
        return 0.0


def _is_hodling_supply(rpc: Any, currency_name_or_id: str, identity_name_or_id: str) -> bool:
    currency_details = rpc.get_currency(currency_name_or_id)
    if not isinstance(currency_details, dict):
        return False

    state = currency_details.get("lastconfirmedcurrencystate")
    if not isinstance(state, dict):
        return False

    supply = state.get("supply")
    try:
        supply_f = float(supply)
    except Exception:
        return False

    balance = _get_currency_balance_amount(rpc, identity_name_or_id, currency_name_or_id)
    return abs(balance - supply_f) < 1e-12


def _effective_contribution_amount(rpc: Any, currency_name: str, identity_name_or_id: str, requested_amount: float) -> float:
    conversion_fee = float(os.getenv("CURRENCY_CONVERSION_PC_FEE", "0.00025"))
    if _is_hodling_supply(rpc, currency_name, identity_name_or_id):
        return requested_amount * (1 - conversion_fee)
    return requested_amount


def _ensure_initial_contribution(
    rpc: Any,
    *,
    target_identity: str,
    currency_name: str,
    requested_amount: float,
    source_identity: str,
) -> tuple[float, tuple[str, str] | None]:
    amount = _effective_contribution_amount(rpc, currency_name, target_identity, requested_amount)
    current_target = _get_currency_balance_amount(rpc, target_identity, currency_name)
    if current_target >= amount:
        return amount, None

    shortfall = amount - current_target
    source_balance = _get_currency_balance_amount(rpc, source_identity, currency_name)
    if source_balance < shortfall:
        raise Exception(
            f"Insufficient source balance for {currency_name}: need {shortfall}, have {source_balance} at {source_identity}"
        )

    result = rpc.send_currency_simple_to_identity(source_identity, currency_name, target_identity, shortfall)
    return amount, _extract_operation_or_txid(result)


def _process_currency_simple_step(conn: sqlite3.Connection, row: sqlite3.Row, payload: dict, progress: dict, rpc: Any):
    step = row["step_index"]
    name = payload["name"]
    parent = payload["parent"]

    if step == 0:
        rnc_response = rpc.register_name_commitment(
            name,
            payload["primary_raddress"],
            "",
            parent,
            row["source_of_funds"],
        )
        txid = rnc_response.get("txid") if isinstance(rnc_response, dict) else None
        if not txid:
            raise Exception("Name commitment did not return txid")
        progress["rnc_payload"] = rnc_response
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=1,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if step == 1:
        full_name = f"{name}.{parent}"
        identity_payload = _build_identity_payload(full_name, payload["primary_raddress"])
        fee_offer = _resolve_fee_offer(rpc, parent)
        txid = rpc.register_identity(
            progress.get("rnc_payload", {}),
            identity_payload,
            row["source_of_funds"],
            fee_offer,
        )
        if not isinstance(txid, str) or not txid:
            raise Exception("register_identity did not return txid")
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=2,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if step == 2:
        result = rpc.send_currency_simple_to_identity(
            payload["primary_raddress"],
            payload["native_coin"],
            f"{name}@",
            payload["define_funding_amount"],
        )
        wait_type, wait_value = _extract_operation_or_txid(result)
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_opid" if wait_type == "opid_txid" else "waiting_confirm",
            step_index=3,
            progress=progress,
            wait_type=wait_type,
            wait_value=wait_value,
        )
        return

    if step == 3:
        txid = rpc.define_simple_token_currency(
            payload["define_options"],
            name,
            payload["id_registration_fees"],
            [{payload["pre_allocation_id"]: payload["pre_allocation_amount"]}],
            payload["proof_protocol"],
        )
        if not isinstance(txid, str) or not txid:
            raise Exception("define simple token did not return txid")
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=4,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if step == 4:
        _save_currency_state(
            conn,
            row["id"],
            status="complete",
            step_index=4,
            progress=progress,
            wait_type=None,
            wait_value=None,
            error_message=None,
        )
        return

    raise Exception(f"Unsupported simple_token step index: {step}")


def _process_fractional_reserve_step(conn: sqlite3.Connection, row: sqlite3.Row, payload: dict, progress: dict, rpc: Any):
    reserves = payload.get("reserves", [])
    reserve_index = int(progress.get("reserve_index", 0))
    reserve_phase = int(progress.get("reserve_phase", 0))
    if reserve_index >= len(reserves):
        progress["reserve_index"] = reserve_index
        progress["reserve_phase"] = reserve_phase
        _save_currency_state(conn, row["id"], status="in_progress", step_index=4, progress=progress)
        return

    reserve = reserves[reserve_index]
    reserve_name = reserve["name"]
    parent = payload["parent"]
    reserve_progress = progress.setdefault("reserves", {}).setdefault(reserve_name, {})

    if reserve_phase == 0:
        rnc_response = rpc.register_name_commitment(
            reserve_name,
            payload["primary_raddress"],
            "",
            parent,
            row["source_of_funds"],
        )
        txid = rnc_response.get("txid") if isinstance(rnc_response, dict) else None
        if not txid:
            raise Exception(f"Reserve {reserve_name} name commitment did not return txid")
        reserve_progress["rnc_payload"] = rnc_response
        progress["reserve_phase"] = 1
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=3,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if reserve_phase == 1:
        full_name = f"{reserve_name}.{parent}"
        identity_payload = _build_identity_payload(full_name, payload["primary_raddress"])
        fee_offer = _resolve_fee_offer(rpc, parent)
        txid = rpc.register_identity(
            reserve_progress.get("rnc_payload", {}),
            identity_payload,
            row["source_of_funds"],
            fee_offer,
        )
        if not isinstance(txid, str) or not txid:
            raise Exception(f"Reserve {reserve_name} register_identity did not return txid")
        progress["reserve_phase"] = 2
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=3,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if reserve_phase == 2:
        result = rpc.send_currency_simple_to_identity(
            payload["primary_raddress"],
            payload["native_coin"],
            f"{reserve_name}@",
            payload["define_funding_amount"],
        )
        wait_type, wait_value = _extract_operation_or_txid(result)
        progress["reserve_phase"] = 3
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_opid" if wait_type == "opid_txid" else "waiting_confirm",
            step_index=3,
            progress=progress,
            wait_type=wait_type,
            wait_value=wait_value,
        )
        return

    if reserve_phase == 3:
        txid = rpc.define_simple_token_currency(
            32,
            reserve_name,
            payload.get("id_registration_fees", 50),
            [{payload["allocation_id"]: reserve["supply"]}],
            1,
        )
        if not isinstance(txid, str) or not txid:
            raise Exception(f"Reserve {reserve_name} definecurrency did not return txid")
        progress["reserve_phase"] = 4
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=3,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if reserve_phase == 4:
        progress["reserve_index"] = reserve_index + 1
        progress["reserve_phase"] = 0
        _save_currency_state(conn, row["id"], status="in_progress", step_index=3, progress=progress)
        return

    raise Exception(f"Unsupported reserve phase: {reserve_phase}")


def _process_currency_fractional_step(conn: sqlite3.Connection, row: sqlite3.Row, payload: dict, progress: dict, rpc: Any):
    step = row["step_index"]
    name = payload["name"]
    parent = payload["parent"]
    prepare_fractional_identity = bool(payload.get("prepare_fractional_identity", True))
    create_reserves = bool(payload.get("create_reserves", True))

    if step == 0:
        if not prepare_fractional_identity:
            _save_currency_state(conn, row["id"], status="in_progress", step_index=3, progress=progress)
            return

        rnc_response = rpc.register_name_commitment(
            name,
            payload["primary_raddress"],
            "",
            parent,
            row["source_of_funds"],
        )
        txid = rnc_response.get("txid") if isinstance(rnc_response, dict) else None
        if not txid:
            raise Exception("Fractional name commitment did not return txid")
        progress["fractional_rnc_payload"] = rnc_response
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=1,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if step == 1:
        full_name = f"{name}.{parent}"
        identity_payload = _build_identity_payload(full_name, payload["primary_raddress"])
        fee_offer = _resolve_fee_offer(rpc, parent)
        txid = rpc.register_identity(
            progress.get("fractional_rnc_payload", {}),
            identity_payload,
            row["source_of_funds"],
            fee_offer,
        )
        if not isinstance(txid, str) or not txid:
            raise Exception("Fractional register_identity did not return txid")
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=2,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if step == 2:
        result = rpc.send_currency_simple_to_identity(
            payload["primary_raddress"],
            payload["native_coin"],
            f"{name}@",
            payload["define_funding_amount"],
        )
        wait_type, wait_value = _extract_operation_or_txid(result)
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_opid" if wait_type == "opid_txid" else "waiting_confirm",
            step_index=3,
            progress=progress,
            wait_type=wait_type,
            wait_value=wait_value,
        )
        return

    if step == 3:
        if not create_reserves or not payload.get("reserves"):
            _save_currency_state(conn, row["id"], status="in_progress", step_index=4, progress=progress)
            return
        _process_fractional_reserve_step(conn, row, payload, progress, rpc)
        return

    if step == 4:
        contributions = progress.setdefault("effective_initial_contributions", {})
        native = payload["native"]
        reserves = payload.get("reserves", [])
        fund_index = int(progress.get("fund_index", 0))
        target_identity = f"{name}@"

        if fund_index == 0:
            native_amount, wait = _ensure_initial_contribution(
                rpc,
                target_identity=target_identity,
                currency_name=native["name"],
                requested_amount=native["initial_contribution"],
                source_identity=payload["primary_raddress"],
            )
            contributions["native"] = native_amount
            progress["effective_initial_contributions"] = contributions
            if wait is not None:
                wait_type, wait_value = wait
                _save_currency_state(
                    conn,
                    row["id"],
                    status="waiting_opid" if wait_type == "opid_txid" else "waiting_confirm",
                    step_index=4,
                    progress=progress,
                    wait_type=wait_type,
                    wait_value=wait_value,
                )
                return
            progress["fund_index"] = 1
            _save_currency_state(conn, row["id"], status="in_progress", step_index=4, progress=progress)
            return

        reserve_pos = fund_index - 1
        if reserve_pos < len(reserves):
            reserve = reserves[reserve_pos]
            reserve_amount, wait = _ensure_initial_contribution(
                rpc,
                target_identity=target_identity,
                currency_name=reserve["name"],
                requested_amount=reserve["initial_contribution"],
                source_identity=payload["allocation_id"],
            )
            reserve_effective = contributions.setdefault("reserves", {})
            reserve_effective[reserve["name"]] = reserve_amount
            progress["effective_initial_contributions"] = contributions
            if wait is not None:
                wait_type, wait_value = wait
                _save_currency_state(
                    conn,
                    row["id"],
                    status="waiting_opid" if wait_type == "opid_txid" else "waiting_confirm",
                    step_index=4,
                    progress=progress,
                    wait_type=wait_type,
                    wait_value=wait_value,
                )
                return
            progress["fund_index"] = fund_index + 1
            _save_currency_state(conn, row["id"], status="in_progress", step_index=4, progress=progress)
            return

        _save_currency_state(conn, row["id"], status="in_progress", step_index=5, progress=progress)
        return

    if step == 5:
        native = payload["native"]
        reserves = payload.get("reserves", [])
        effective = progress.get("effective_initial_contributions", {})
        reserve_effective = effective.get("reserves", {}) if isinstance(effective.get("reserves"), dict) else {}
        initial_contributions = [effective.get("native", native["initial_contribution"])]

        currencies = [native["name"]]
        weights = [native["weight"]]
        for reserve in reserves:
            currencies.append(reserve["name"])
            weights.append(reserve["weight"])
            initial_contributions.append(reserve_effective.get(reserve["name"], reserve["initial_contribution"]))

        options = {
            "name": name,
            "options": 33,
            "idregistrationfees": payload["id_registration_fees"],
            "idreferrallevels": payload["id_referral_levels"],
            "startblock": payload["start_block"],
            "currencies": currencies,
            "weights": weights,
            "initialcontributions": initial_contributions,
            "initialsupply": payload["initial_supply"],
        }

        txid = rpc.define_currency(options)
        if not isinstance(txid, str) or not txid:
            raise Exception("Fractional definecurrency did not return txid")
        _save_currency_state(
            conn,
            row["id"],
            status="waiting_confirm",
            step_index=6,
            progress=progress,
            wait_type="tx_confirm",
            wait_value=txid,
        )
        return

    if step == 6:
        _save_currency_state(conn, row["id"], status="complete", step_index=6, progress=progress)
        return

    raise Exception(f"Unsupported fractional_token step index: {step}")


def process_currency_once() -> int:
    conn = _get_db_connection()
    rows = conn.execute(
        """
        SELECT *
        FROM currency_requests
        WHERE status IN ('pending', 'in_progress', 'waiting_confirm', 'waiting_opid')
          AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
        ORDER BY datetime(updated_at) ASC
        """
    ).fetchall()

    updated_count = 0
    for row in rows:
        row_status = row["status"]
        try:
            rpc = _get_rpc_connection(row["daemon_name"])

            if row_status == "waiting_confirm":
                txid = row["wait_value"]
                if not txid:
                    raise Exception("Missing wait txid while in waiting_confirm state")

                tx = rpc.get_raw_transaction(txid)
                confirmations = tx.get("confirmations", 0) if isinstance(tx, dict) else 0
                if confirmations > 0:
                    _save_currency_state(
                        conn,
                        row["id"],
                        status="in_progress",
                        step_index=row["step_index"],
                        progress=_parse_json_or_empty(row["progress_json"]),
                        wait_type=None,
                        wait_value=None,
                    )
                    updated_count += 1
                continue

            if row_status == "waiting_opid":
                opid = row["wait_value"]
                if not opid:
                    raise Exception("Missing operation id while in waiting_opid state")

                txid = _poll_operation_for_txid(rpc, opid)
                if txid:
                    _save_currency_state(
                        conn,
                        row["id"],
                        status="waiting_confirm",
                        step_index=row["step_index"],
                        progress=_parse_json_or_empty(row["progress_json"]),
                        wait_type="tx_confirm",
                        wait_value=txid,
                    )
                    updated_count += 1
                continue

            if row_status == "pending":
                conn.execute(
                    """
                    UPDATE currency_requests
                    SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                row = conn.execute("SELECT * FROM currency_requests WHERE id = ?", (row["id"],)).fetchone()

            payload = _parse_json_or_empty(row["payload_json"])
            progress = _parse_json_or_empty(row["progress_json"])
            workflow_type = row["workflow_type"]

            if workflow_type == "simple_token":
                _process_currency_simple_step(conn, row, payload, progress, rpc)
                updated_count += 1
            elif workflow_type == "fractional_token":
                _process_currency_fractional_step(conn, row, payload, progress, rpc)
                updated_count += 1
            else:
                raise Exception(f"Unsupported workflow type: {workflow_type}")
        except Exception as exc:
            _record_currency_retry_or_failure(
                conn,
                row["id"],
                row["attempts"],
                str(exc),
                row_status if row_status in {"pending", "in_progress", "waiting_confirm", "waiting_opid"} else "in_progress",
            )
            updated_count += 1

    conn.commit()
    conn.close()
    return updated_count


def _record_storage_retry_or_failure(conn: sqlite3.Connection, upload_id: str, attempts: int, error: str, status: str) -> bool:
    max_retries, base_seconds = _retry_config()
    next_attempt = attempts + 1

    if next_attempt >= max_retries:
        conn.execute(
            """
            UPDATE storage_uploads
            SET status = 'failed', attempts = ?, error_message = ?, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_attempt, error, upload_id),
        )
        return False

    delay_seconds = base_seconds * (2 ** (next_attempt - 1))
    conn.execute(
        """
        UPDATE storage_uploads
        SET status = ?, attempts = ?, error_message = ?, next_retry_at = datetime('now', ?), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, next_attempt, error, f"+{delay_seconds} seconds", upload_id),
    )
    return True


def process_next_storage_chunk(upload_id: str, rpc_connection: Any) -> dict:
    """Submit exactly one pending storage chunk for an upload.

    This is a single-step sequenced primitive used by Phase 1 tests and
    upcoming storage worker orchestration.
    """
    conn = _get_db_connection()
    try:
        upload = conn.execute(
            """
            SELECT id, identity_fqn, status
            FROM storage_uploads
            WHERE id = ?
            """,
            (upload_id,),
        ).fetchone()

        if upload is None:
            return {"submitted": 0, "reason": "upload_not_found"}

        if upload["status"] not in {"uploading", "confirming"}:
            return {"submitted": 0, "reason": "invalid_upload_state"}

        next_chunk = conn.execute(
            """
            SELECT id, chunk_index, vdxf_key, status
            FROM storage_chunks
            WHERE upload_id = ? AND status = 'pending'
            ORDER BY chunk_index ASC
            LIMIT 1
            """,
            (upload_id,),
        ).fetchone()

        if next_chunk is None:
            return {"submitted": 0, "reason": "no_pending_chunks"}

        payload = {
            "name": upload["identity_fqn"],
            "contentmultimap": {
                next_chunk["vdxf_key"]: [
                    {
                        "data": {
                            "address": upload["identity_fqn"],
                            "filename": f"/tmp/{upload_id}_{next_chunk['chunk_index']}",
                            "createmmr": True,
                            "mimetype": "application/octet-stream",
                            "label": f"chunk-{next_chunk['chunk_index']}",
                        }
                    }
                ]
            },
        }

        result = rpc_connection.update_identity(payload)
        txid = None
        if isinstance(result, dict):
            txid = result.get("txid")

        conn.execute(
            """
            UPDATE storage_chunks
            SET status = 'submitted', txid = ?
            WHERE id = ?
            """,
            (txid, next_chunk["id"]),
        )
        conn.execute(
            """
            UPDATE storage_uploads
            SET status = 'confirming', current_chunk_index = ?
            WHERE id = ?
            """,
            (next_chunk["chunk_index"], upload_id),
        )
        conn.commit()
        return {"submitted": 1, "txid": txid, "chunk_index": next_chunk["chunk_index"]}
    finally:
        conn.close()


def process_storage_upload_once(upload_id: str, rpc_connection: Any) -> dict:
    """Process one storage upload step.

    Order of operations:
    1) Confirm any previously submitted chunks.
    2) If all chunks are confirmed, mark upload complete.
    3) Otherwise submit at most one pending chunk.
    """
    conn = _get_db_connection()
    try:
        upload = conn.execute(
            "SELECT id, identity_fqn, status, attempts FROM storage_uploads WHERE id = ?",
            (upload_id,),
        ).fetchone()
        if upload is None:
            return {"state": "upload_not_found", "submitted": 0}

        # Step 1: confirm previously submitted chunks.
        submitted_chunks = conn.execute(
            """
            SELECT id, chunk_index, txid
            FROM storage_chunks
            WHERE upload_id = ? AND status = 'submitted'
            ORDER BY chunk_index ASC
            """,
            (upload_id,),
        ).fetchall()

        for chunk in submitted_chunks:
            txid = chunk["txid"]
            if not txid or not hasattr(rpc_connection, "get_raw_transaction"):
                continue
            tx = rpc_connection.get_raw_transaction(txid)
            confirmations = tx.get("confirmations", 0) if isinstance(tx, dict) else 0
            if confirmations > 0:
                conn.execute(
                    "UPDATE storage_chunks SET status = 'confirmed' WHERE id = ?",
                    (chunk["id"],),
                )

        # Step 2: complete if all chunks are confirmed.
        summary = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed_count,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) AS submitted_count
            FROM storage_chunks
            WHERE upload_id = ?
            """,
            (upload_id,),
        ).fetchone()

        confirmed_count = summary["confirmed_count"] or 0
        pending_count = summary["pending_count"] or 0
        submitted_count = summary["submitted_count"] or 0
        total_count = confirmed_count + pending_count + submitted_count

        if total_count > 0 and confirmed_count == total_count:
            conn.execute(
                "UPDATE storage_uploads SET status = 'complete', attempts = 0, next_retry_at = NULL, error_message = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (upload_id,),
            )
            conn.commit()
            return {"state": "complete", "submitted": 0}

        # Step 3: submit at most one pending chunk.
        if pending_count > 0:
            next_chunk = conn.execute(
                """
                SELECT id, chunk_index, vdxf_key
                FROM storage_chunks
                WHERE upload_id = ? AND status = 'pending'
                ORDER BY chunk_index ASC
                LIMIT 1
                """,
                (upload_id,),
            ).fetchone()

            payload = {
                "name": upload["identity_fqn"],
                "contentmultimap": {
                    next_chunk["vdxf_key"]: [
                        {
                            "data": {
                                "address": upload["identity_fqn"],
                                "filename": f"/tmp/{upload_id}_{next_chunk['chunk_index']}",
                                "createmmr": True,
                                "mimetype": "application/octet-stream",
                                "label": f"chunk-{next_chunk['chunk_index']}",
                            }
                        }
                    ]
                },
            }

            try:
                result = rpc_connection.update_identity(payload)
            except Exception as exc:
                error_message = str(exc)
                if _is_permanent_storage_error(error_message):
                    conn.execute(
                        """
                        UPDATE storage_uploads
                        SET status = 'failed', attempts = attempts + 1, error_message = ?, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (error_message, upload_id),
                    )
                    conn.commit()
                    return {"state": "failed", "submitted": 0, "error": error_message}

                retry_scheduled = _record_storage_retry_or_failure(
                    conn,
                    upload_id,
                    upload["attempts"],
                    error_message,
                    upload["status"] or "uploading",
                )
                conn.commit()
                if retry_scheduled:
                    return {"state": "retry_scheduled", "submitted": 0, "error": error_message}
                return {"state": "failed", "submitted": 0, "error": error_message}

            txid = result.get("txid") if isinstance(result, dict) else None
            conn.execute(
                "UPDATE storage_chunks SET status = 'submitted', txid = ? WHERE id = ?",
                (txid, next_chunk["id"]),
            )
            conn.execute(
                """
                UPDATE storage_uploads
                SET status = 'confirming', current_chunk_index = ?, attempts = 0, next_retry_at = NULL, error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (next_chunk["chunk_index"], upload_id),
            )
            conn.commit()
            return {"state": "confirming", "submitted": 1, "chunk_index": next_chunk["chunk_index"], "txid": txid}

        # No pending chunks and not complete yet -> remain in confirming.
        conn.execute(
            "UPDATE storage_uploads SET status = 'confirming', attempts = 0, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (upload_id,),
        )
        conn.commit()
        return {"state": "confirming", "submitted": 0}
    finally:
        conn.close()


def process_storage_once() -> int:
    """Process one sweep of due storage uploads in uploading/confirming states."""
    conn = _get_db_connection()
    rows = conn.execute(
        """
        SELECT id, daemon_name
        FROM storage_uploads
        WHERE status IN ('uploading', 'confirming')
          AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
        ORDER BY datetime(updated_at) ASC
        """
    ).fetchall()
    conn.close()

    processed = 0
    for row in rows:
        try:
            rpc = _get_rpc_connection(row["daemon_name"])
            process_storage_upload_once(row["id"], rpc)
            processed += 1
        except Exception:
            # Keep sweep resilient; per-upload errors are handled by per-upload processor.
            continue

    return processed


def process_once() -> int:
    """Process one worker sweep over pending commitment confirmations.

    Returns the number of rows that were advanced to the next state.
    """
    logger.info("worker.process_once.start")
    conn = _get_db_connection()
    ready_rows = conn.execute(
        """
        SELECT
            id,
            requested_name,
            parent_namespace,
            daemon_name,
            primary_raddress,
            source_of_funds,
            rnc_payload_json,
            attempts
        FROM registrations
        WHERE status = 'ready_for_idr'
          AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
        """
    ).fetchall()

    pending_rows = conn.execute(
        """
                SELECT id, daemon_name, rnc_txid, attempts
        FROM registrations
        WHERE status = 'pending_rnc_confirm'
                    AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
        """
    ).fetchall()

    submitted_rows = conn.execute(
        """
                SELECT id, daemon_name, idr_txid, attempts
        FROM registrations
        WHERE status = 'idr_submitted'
                    AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
        """
    ).fetchall()

    webhook_rows = conn.execute(
        """
        SELECT
            id,
            status,
            requested_name,
            parent_namespace,
            rnc_txid,
            idr_txid,
            error_message,
            webhook_url,
            webhook_secret,
            webhook_attempts
        FROM registrations
        WHERE status IN ('complete', 'failed')
          AND webhook_url IS NOT NULL
          AND webhook_delivered = 0
          AND (webhook_next_retry_at IS NULL OR webhook_next_retry_at <= CURRENT_TIMESTAMP)
        """
    ).fetchall()

    logger.info(
        "worker.process_once.rows_loaded ready_for_idr=%s pending_rnc_confirm=%s idr_submitted=%s webhook_pending=%s",
        len(ready_rows),
        len(pending_rows),
        len(submitted_rows),
        len(webhook_rows),
    )

    updated_count = 0
    for row in pending_rows:
        logger.debug(
            "worker.process_once.pending_rnc_confirm.check row=%s",
            _log_json(_to_log_dict(row)),
        )
        try:
            rpc = _get_rpc_connection(row["daemon_name"])
            tx = rpc.get_raw_transaction(row["rnc_txid"])
            confirmations = 0
            if isinstance(tx, dict):
                confirmations = tx.get("confirmations", 0)
            logger.debug(
                "worker.process_once.pending_rnc_confirm.rpc_response request_id=%s rnc_txid=%s confirmations=%s tx=%s",
                row["id"],
                row["rnc_txid"],
                confirmations,
                _log_json(tx),
            )
        except Exception as exc:
            logger.exception(
                "worker.process_once.pending_rnc_confirm.rpc_error request_id=%s daemon=%s rnc_txid=%s",
                row["id"],
                row["daemon_name"],
                row["rnc_txid"],
            )
            _record_retry_or_failure(conn, row["id"], row["attempts"], str(exc), "pending_rnc_confirm")
            updated_count += 1
            continue

        if confirmations > 0:
            conn.execute(
                """
                UPDATE registrations
                SET status = ?, attempts = 0, error_message = NULL, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("ready_for_idr", row["id"]),
            )
            logger.info(
                "worker.process_once.pending_rnc_confirm.promoted request_id=%s from=pending_rnc_confirm to=ready_for_idr confirmations=%s",
                row["id"],
                confirmations,
            )
            updated_count += 1
        else:
            logger.debug(
                "worker.process_once.pending_rnc_confirm.waiting request_id=%s confirmations=%s",
                row["id"],
                confirmations,
            )

    for row in ready_rows:
        logger.debug(
            "worker.process_once.ready_for_idr.start row=%s",
            _log_json(_to_log_dict(row)),
        )
        rpc = _get_rpc_connection(row["daemon_name"])
        rnc_payload = json.loads(row["rnc_payload_json"])
        full_name = f'{row["requested_name"]}.{row["parent_namespace"]}'
        identity_payload = _build_identity_payload(full_name, row["primary_raddress"])
        fee_offer = _resolve_fee_offer(rpc, row["parent_namespace"])
        logger.debug(
            "worker.process_once.ready_for_idr.computed request_id=%s full_name=%s rnc_payload=%s identity_payload=%s fee_offer=%s",
            row["id"],
            full_name,
            _log_json(rnc_payload),
            _log_json(identity_payload),
            fee_offer,
        )

        try:
            txid = rpc.register_identity(
                rnc_payload,
                identity_payload,
                row["source_of_funds"],
                fee_offer,
            )
            logger.info(
                "worker.process_once.ready_for_idr.submitted request_id=%s to=idr_submitted idr_txid=%s",
                row["id"],
                txid,
            )
            conn.execute(
                """
                UPDATE registrations
                SET status = ?, idr_txid = ?, attempts = 0, error_message = NULL, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("idr_submitted", txid, row["id"]),
            )
        except Exception as exc:
            logger.exception(
                "worker.process_once.ready_for_idr.submit_error request_id=%s daemon=%s full_name=%s",
                row["id"],
                row["daemon_name"],
                full_name,
            )
            _record_retry_or_failure(conn, row["id"], row["attempts"], str(exc), "ready_for_idr")
        updated_count += 1

    for row in submitted_rows:
        logger.debug(
            "worker.process_once.idr_submitted.check row=%s",
            _log_json(_to_log_dict(row)),
        )
        try:
            rpc = _get_rpc_connection(row["daemon_name"])
            tx = rpc.get_raw_transaction(row["idr_txid"])
            confirmations = 0
            if isinstance(tx, dict):
                confirmations = tx.get("confirmations", 0)
            logger.debug(
                "worker.process_once.idr_submitted.rpc_response request_id=%s idr_txid=%s confirmations=%s tx=%s",
                row["id"],
                row["idr_txid"],
                confirmations,
                _log_json(tx),
            )
        except Exception as exc:
            logger.exception(
                "worker.process_once.idr_submitted.rpc_error request_id=%s daemon=%s idr_txid=%s",
                row["id"],
                row["daemon_name"],
                row["idr_txid"],
            )
            _record_retry_or_failure(conn, row["id"], row["attempts"], str(exc), "idr_submitted")
            updated_count += 1
            continue

        if confirmations > 0:
            conn.execute(
                """
                UPDATE registrations
                SET status = ?, attempts = 0, error_message = NULL, next_retry_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("complete", row["id"]),
            )
            logger.info(
                "worker.process_once.idr_submitted.completed request_id=%s from=idr_submitted to=complete confirmations=%s",
                row["id"],
                confirmations,
            )
            updated_count += 1
        else:
            logger.debug(
                "worker.process_once.idr_submitted.waiting request_id=%s confirmations=%s",
                row["id"],
                confirmations,
            )

    webhook_timeout_seconds = int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "5"))
    fallback_secret = os.getenv("WEBHOOK_SIGNING_SECRET", "")

    for row in webhook_rows:
        logger.debug(
            "worker.process_once.webhook.start row=%s",
            _log_json(_to_log_dict(row, redacted_keys={"webhook_secret"})),
        )
        payload = {
            "event": f"registration.{row['status']}",
            "request_id": row["id"],
            "status": row["status"],
            "name": row["requested_name"],
            "parent": row["parent_namespace"],
            "full_id": f"{row['requested_name']}.{row['parent_namespace']}@",
            "txid_rnc": row["rnc_txid"],
            "txid_idr": row["idr_txid"],
            "error": row["error_message"],
        }
        secret = row["webhook_secret"] or fallback_secret
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": payload["event"],
        }
        if secret:
            headers["X-Webhook-Signature"] = _webhook_signature(secret, payload)

        logger.debug(
            "worker.process_once.webhook.prepared request_id=%s payload=%s headers=%s timeout_seconds=%s",
            row["id"],
            _log_json(payload),
            _log_json({
                "Content-Type": headers.get("Content-Type"),
                "X-Webhook-Event": headers.get("X-Webhook-Event"),
                "has_signature": "X-Webhook-Signature" in headers,
            }),
            webhook_timeout_seconds,
        )

        try:
            _post_webhook(row["webhook_url"], payload, headers, webhook_timeout_seconds)
            conn.execute(
                """
                UPDATE registrations
                SET webhook_delivered = 1,
                    webhook_attempts = webhook_attempts + 1,
                    webhook_last_error = NULL,
                    webhook_next_retry_at = NULL,
                    webhook_delivered_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (row["id"],),
            )
            logger.info(
                "worker.process_once.webhook.delivered request_id=%s url=%s status=%s",
                row["id"],
                row["webhook_url"],
                row["status"],
            )
        except Exception as exc:
            logger.exception(
                "worker.process_once.webhook.delivery_error request_id=%s url=%s",
                row["id"],
                row["webhook_url"],
            )
            _record_webhook_retry_or_failure(conn, row["id"], row["webhook_attempts"], str(exc))
        updated_count += 1

    logger.info("worker.process_once.commit updated_count=%s", updated_count)
    conn.commit()
    conn.close()

    storage_updated_count = process_storage_once()
    currency_updated_count = process_currency_once()
    total_updated_count = updated_count + storage_updated_count + currency_updated_count
    logger.info(
        "worker.process_once.finish updated_count=%s storage_updated_count=%s currency_updated_count=%s total=%s",
        updated_count,
        storage_updated_count,
        currency_updated_count,
        total_updated_count,
    )
    return total_updated_count


if __name__ == "__main__":
    advanced = process_once()
    print(f"Advanced rows: {advanced}")
