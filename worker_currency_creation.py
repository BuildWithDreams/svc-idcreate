import json
import logging
import os
import re
import sqlite3
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable

from worker_shared import get_tx_confirmations, poll_operation_for_txid, resolve_wait_progress


logger = logging.getLogger(__name__)


def _safe_log_json(data: Any, max_len: int = 6000) -> str:
    try:
        rendered = json.dumps(data, sort_keys=True, default=str)
    except Exception:
        rendered = str(data)
    if len(rendered) > max_len:
        return f"{rendered[:max_len]}...<truncated>"
    return rendered


def _amount_decimals() -> int:
    raw = os.getenv("CURRENCY_AMOUNT_DECIMALS", "8")
    try:
        value = int(raw)
    except Exception:
        return 8
    return 8 if value < 0 else value


def _amount_epsilon() -> float:
    raw = os.getenv("CURRENCY_AMOUNT_EPSILON", "1e-8")
    try:
        value = float(raw)
    except Exception:
        return 1e-8
    return 1e-8 if value <= 0 else value


def _conversion_pc_fee() -> float:
    raw = os.getenv("CURRENCY_CONVERSION_PC_FEE", "0.0005")
    try:
        value = float(raw)
    except Exception:
        return 0.00025
    return 0.00025 if value < 0 else value


def _normalize_amount(value: float) -> float:
    decimals = _amount_decimals()
    quant = Decimal("1").scaleb(-decimals)
    normalized = Decimal(str(value)).quantize(quant, rounding=ROUND_DOWN)
    return float(normalized)


def _retry_config() -> tuple[int, int]:
    max_retries = int(os.getenv("WORKER_MAX_RETRIES", "5"))
    base_seconds = int(os.getenv("WORKER_RETRY_BASE_SECONDS", "15"))
    return max_retries, base_seconds


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


def _prefer_txid_wait(rpc: Any, wait_type: str, wait_value: str, *, log_context: str) -> tuple[str, str]:
    if wait_type != "opid_txid":
        return wait_type, wait_value

    resolved, next_wait_type, next_wait_value = resolve_wait_progress(rpc, wait_type, wait_value)
    if resolved and next_wait_type == "tx_confirm" and next_wait_value:
        logger.info(
            "currency.wait.opid_resolved context=%s opid=%s txid=%s",
            log_context,
            wait_value,
            next_wait_value,
        )
        return "tx_confirm", next_wait_value

    logger.info("currency.wait.opid_pending context=%s opid=%s", log_context, wait_value)
    return wait_type, wait_value


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


def _get_currency_balance_amount(rpc: Any, holder: str, currency: str) -> float:
    balances = rpc.get_currency_balance(holder)
    if not isinstance(balances, dict):
        return 0.0
    try:
        return float(balances.get(currency, 0.0))
    except Exception:
        return 0.0


def _currency_exists(rpc: Any, currency_name_or_id: str) -> bool:
    try:
        result = rpc.get_currency(currency_name_or_id)
        return isinstance(result, dict) and bool(result)
    except Exception:
        return False


def _is_hodling_supply(rpc: Any, currency_name_or_id: str, identity_name_or_id: str) -> bool:
    currency_details = rpc.get_currency(currency_name_or_id)
    if not isinstance(currency_details, dict):
        return False

    state = currency_details.get("lastconfirmedcurrencystate")
    if not isinstance(state, dict):
        return False

    supply = state.get("supply")
    try:
        supply_f = float(0 if supply is None else supply)
    except Exception:
        return False

    balance = _get_currency_balance_amount(rpc, identity_name_or_id, currency_name_or_id)
    return abs(balance - supply_f) < 1e-12


def _effective_contribution_amount(requested_amount: float, apply_conversion_fee: bool) -> float:
    if not apply_conversion_fee:
        return _normalize_amount(requested_amount)

    conversion_fee = _conversion_pc_fee()
    return _normalize_amount(requested_amount * (1 - conversion_fee))


def _compute_funding_shortfall(
    rpc: Any,
    *,
    target_identity: str,
    currency_name: str,
    required_amount: float,
    source_identity: str,
    context_hint: str | None = None,
) -> tuple[float, float]:
    epsilon = _amount_epsilon()
    required = _normalize_amount(required_amount)
    current_target = _get_currency_balance_amount(rpc, target_identity, currency_name)
    shortfall = _normalize_amount(required - current_target)

    logger.info(
        "currency.contribution.check currency=%s source=%s target=%s required=%s current_target=%s shortfall=%s context=%s",
        currency_name,
        source_identity,
        target_identity,
        required,
        current_target,
        shortfall,
        context_hint,
    )

    if shortfall <= epsilon:
        return required, 0.0

    source_balance = _get_currency_balance_amount(rpc, source_identity, currency_name)
    if source_balance + epsilon < shortfall:
        exists = _currency_exists(rpc, currency_name)
        logger.error(
            "currency.contribution.insufficient currency=%s exists=%s source=%s source_balance=%s shortfall=%s target=%s context=%s",
            currency_name,
            exists,
            source_identity,
            source_balance,
            shortfall,
            target_identity,
            context_hint,
        )
        raise Exception(
            f"Insufficient source balance for {currency_name}: need {shortfall}, have {source_balance} at {source_identity}; "
            f"currency_exists={exists}; target={target_identity}; context={context_hint}"
        )

    return required, shortfall


def _submit_funding_transfers(rpc: Any, source_identity: str, params: list[dict[str, Any]]) -> list[tuple[str, str]]:
    if not params:
        return []

    logger.info(
        "currency.rpc.send_currency.submit from=%s outputs=%s params=%s",
        source_identity,
        len(params),
        _safe_log_json(params),
    )

    send_currency_fn = getattr(rpc, "send_currency", None)
    if callable(send_currency_fn):
        result = send_currency_fn(source_identity, params)
        wait_type, wait_value = _extract_operation_or_txid(result)
        wait_type, wait_value = _prefer_txid_wait(
            rpc,
            wait_type,
            wait_value,
            log_context=f"batch_funding source={source_identity}",
        )
        return [(wait_type, wait_value)]

    waits: list[tuple[str, str]] = []
    for item in params:
        result = rpc.send_currency_simple_to_identity(source_identity, item["currency"], item["address"], item["amount"])
        wait_type, wait_value = _extract_operation_or_txid(result)
        wait_type, wait_value = _prefer_txid_wait(
            rpc,
            wait_type,
            wait_value,
            log_context=(
                f"single_funding source={source_identity} currency={item['currency']} "
                f"address={item['address']} amount={item['amount']}"
            ),
        )
        waits.append((wait_type, wait_value))
    return waits


def _ensure_initial_contribution(
    rpc: Any,
    *,
    target_identity: str,
    currency_name: str,
    requested_amount: float,
    source_identity: str,
    context_hint: str | None = None,
    wait_for_confirmation: bool = True,
    apply_conversion_fee: bool = False,
) -> tuple[float, tuple[str, str] | None]:
    epsilon = _amount_epsilon()
    amount = _effective_contribution_amount(requested_amount, apply_conversion_fee)
    current_target = _get_currency_balance_amount(rpc, target_identity, currency_name)
    logger.info(
        "currency.contribution.check currency=%s source=%s target=%s requested=%s effective=%s current_target=%s context=%s",
        currency_name,
        source_identity,
        target_identity,
        requested_amount,
        amount,
        current_target,
        context_hint,
    )
    shortfall_raw = amount - current_target
    shortfall = _normalize_amount(shortfall_raw)
    if shortfall <= epsilon:
        logger.info(
            "currency.contribution.satisfied currency=%s target=%s effective=%s current_target=%s context=%s",
            currency_name,
            target_identity,
            amount,
            current_target,
            context_hint,
        )
        return amount, None

    source_balance = _get_currency_balance_amount(rpc, source_identity, currency_name)
    logger.info(
        "currency.contribution.shortfall currency=%s source=%s target=%s shortfall=%s source_balance=%s context=%s",
        currency_name,
        source_identity,
        target_identity,
        shortfall,
        source_balance,
        context_hint,
    )
    if source_balance + epsilon < shortfall:
        exists = _currency_exists(rpc, currency_name)
        logger.error(
            "currency.contribution.insufficient currency=%s exists=%s source=%s source_balance=%s shortfall=%s target=%s context=%s",
            currency_name,
            exists,
            source_identity,
            source_balance,
            shortfall,
            target_identity,
            context_hint,
        )
        raise Exception(
            f"Insufficient source balance for {currency_name}: need {shortfall}, have {source_balance} at {source_identity}; "
            f"currency_exists={exists}; target={target_identity}; context={context_hint}"
        )

    if shortfall <= epsilon:
        logger.info(
            "currency.contribution.skip_dust currency=%s source=%s target=%s shortfall=%s epsilon=%s context=%s",
            currency_name,
            source_identity,
            target_identity,
            shortfall,
            epsilon,
            context_hint,
        )
        return amount, None

    logger.info(
        "currency.rpc.send_currency_simple_to_identity.submit from=%s currency=%s to=%s amount=%s context=%s",
        source_identity,
        currency_name,
        target_identity,
        shortfall,
        context_hint,
    )
    result = rpc.send_currency_simple_to_identity(source_identity, currency_name, target_identity, shortfall)
    wait = _extract_operation_or_txid(result)
    logger.info(
        "currency.contribution.transfer_submitted currency=%s source=%s target=%s amount=%s context=%s result=%s",
        currency_name,
        source_identity,
        target_identity,
        shortfall,
        context_hint,
        json.dumps(result, sort_keys=True, default=str),
    )
    if not wait_for_confirmation:
        return amount, None
    return amount, wait


def _process_currency_simple_step(conn: sqlite3.Connection, row: sqlite3.Row, payload: dict, progress: dict, rpc: Any):
    step = row["step_index"]
    name = payload["name"]
    parent = payload["parent"]
    logger.info("currency.simple.step request_id=%s step=%s name=%s parent=%s", row["id"], step, name, parent)

    if step == 0:
        logger.info("currency.simple.rnc.submit request_id=%s name=%s parent=%s", row["id"], name, parent)
        logger.info(
            "currency.rpc.register_name_commitment.submit request_id=%s params=%s",
            row["id"],
            _safe_log_json(
                {
                    "name": name,
                    "control_address": payload["primary_raddress"],
                    "referral_id": "",
                    "parent": parent,
                    "source_of_funds": row["source_of_funds"],
                }
            ),
        )
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
        logger.info("currency.simple.rnc.submitted request_id=%s txid=%s", row["id"], txid)
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
        logger.info(
            "currency.simple.idr.submit request_id=%s full_name=%s fee_offer=%s source_of_funds=%s",
            row["id"],
            full_name,
            fee_offer,
            row["source_of_funds"],
        )
        logger.info(
            "currency.rpc.register_identity.submit request_id=%s full_name=%s params=%s",
            row["id"],
            full_name,
            _safe_log_json(
                {
                    "rnc_payload": progress.get("rnc_payload", {}),
                    "identity_payload": identity_payload,
                    "source_of_funds": row["source_of_funds"],
                    "fee_offer": fee_offer,
                }
            ),
        )
        txid = rpc.register_identity(
            progress.get("rnc_payload", {}),
            identity_payload,
            row["source_of_funds"],
            fee_offer,
        )
        if not isinstance(txid, str) or not txid:
            raise Exception("register_identity did not return txid")
        logger.info("currency.simple.idr.submitted request_id=%s txid=%s", row["id"], txid)
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
        logger.info(
            "currency.rpc.send_currency_simple_to_identity.submit request_id=%s params=%s",
            row["id"],
            _safe_log_json(
                {
                    "from_address": payload["primary_raddress"],
                    "currency": payload["native_coin"],
                    "identity": f"{name}@",
                    "amount": payload["define_funding_amount"],
                }
            ),
        )
        result = rpc.send_currency_simple_to_identity(
            payload["primary_raddress"],
            payload["native_coin"],
            f"{name}@",
            payload["define_funding_amount"],
        )
        wait_type, wait_value = _extract_operation_or_txid(result)
        wait_type, wait_value = _prefer_txid_wait(
            rpc,
            wait_type,
            wait_value,
            log_context=f"simple_token_define_funding request_id={row['id']}",
        )
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
        logger.info(
            "currency.rpc.define_simple_token_currency.submit request_id=%s params=%s",
            row["id"],
            _safe_log_json(
                {
                    "options": payload["define_options"],
                    "name": name,
                    "id_registration_fees": payload["id_registration_fees"],
                    "pre_allocations": [{payload["pre_allocation_id"]: payload["pre_allocation_amount"]}],
                    "proof_protocol": payload["proof_protocol"],
                }
            ),
        )
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
    reserve_identity_exists = bool(reserve.get("identity_exists", False))
    parent = payload["parent"]
    reserve_progress = progress.setdefault("reserves", {}).setdefault(reserve_name, {})
    logger.info(
        "currency.fractional.reserve.step request_id=%s reserve=%s index=%s phase=%s identity_exists=%s create_reserves=%s",
        row["id"],
        reserve_name,
        reserve_index,
        reserve_phase,
        reserve_identity_exists,
        bool(payload.get("create_reserves", True)),
    )

    if reserve_phase == 0:
        if reserve_identity_exists:
            progress["reserve_phase"] = 2
            _save_currency_state(conn, row["id"], status="in_progress", step_index=3, progress=progress)
            return

        logger.info(
            "currency.fractional.reserve.rnc.submit request_id=%s reserve=%s parent=%s",
            row["id"],
            reserve_name,
            parent,
        )
        logger.info(
            "currency.rpc.register_name_commitment.submit request_id=%s reserve=%s params=%s",
            row["id"],
            reserve_name,
            _safe_log_json(
                {
                    "name": reserve_name,
                    "control_address": payload["primary_raddress"],
                    "referral_id": "",
                    "parent": parent,
                    "source_of_funds": row["source_of_funds"],
                }
            ),
        )
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
        logger.info("currency.fractional.reserve.rnc.submitted request_id=%s reserve=%s txid=%s", row["id"], reserve_name, txid)
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
        if reserve_identity_exists:
            progress["reserve_phase"] = 2
            _save_currency_state(conn, row["id"], status="in_progress", step_index=3, progress=progress)
            return

        full_name = f"{reserve_name}.{parent}"
        identity_payload = _build_identity_payload(full_name, payload["primary_raddress"])
        fee_offer = _resolve_fee_offer(rpc, parent)
        logger.info(
            "currency.fractional.reserve.idr.submit request_id=%s reserve=%s full_name=%s fee_offer=%s source_of_funds=%s",
            row["id"],
            reserve_name,
            full_name,
            fee_offer,
            row["source_of_funds"],
        )
        logger.info(
            "currency.rpc.register_identity.submit request_id=%s reserve=%s params=%s",
            row["id"],
            reserve_name,
            _safe_log_json(
                {
                    "rnc_payload": reserve_progress.get("rnc_payload", {}),
                    "identity_payload": identity_payload,
                    "source_of_funds": row["source_of_funds"],
                    "fee_offer": fee_offer,
                }
            ),
        )
        txid = rpc.register_identity(
            reserve_progress.get("rnc_payload", {}),
            identity_payload,
            row["source_of_funds"],
            fee_offer,
        )
        if not isinstance(txid, str) or not txid:
            raise Exception(f"Reserve {reserve_name} register_identity did not return txid")
        logger.info("currency.fractional.reserve.idr.submitted request_id=%s reserve=%s txid=%s", row["id"], reserve_name, txid)
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
        logger.info(
            "currency.rpc.send_currency_simple_to_identity.submit request_id=%s reserve=%s params=%s",
            row["id"],
            reserve_name,
            _safe_log_json(
                {
                    "from_address": payload["primary_raddress"],
                    "currency": payload["native_coin"],
                    "identity": f"{reserve_name}@",
                    "amount": payload["define_funding_amount"],
                }
            ),
        )
        result = rpc.send_currency_simple_to_identity(
            payload["primary_raddress"],
            payload["native_coin"],
            f"{reserve_name}@",
            payload["define_funding_amount"],
        )
        wait_type, wait_value = _extract_operation_or_txid(result)
        wait_type, wait_value = _prefer_txid_wait(
            rpc,
            wait_type,
            wait_value,
            log_context=f"fractional_reserve_define_funding request_id={row['id']} reserve={reserve_name}",
        )
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
        logger.info(
            "currency.rpc.define_simple_token_currency.submit request_id=%s reserve=%s params=%s",
            row["id"],
            reserve_name,
            _safe_log_json(
                {
                    "options": 32,
                    "name": reserve_name,
                    "id_registration_fees": payload.get("id_registration_fees", 50),
                    "pre_allocations": [{payload["allocation_id"]: reserve["supply"]}],
                    "proof_protocol": 1,
                }
            ),
        )
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
    fractional_identity_exists = bool(payload.get("identity_exists", False))
    create_reserves = bool(payload.get("create_reserves", True))
    logger.info(
        "currency.fractional.step request_id=%s step=%s name=%s parent=%s create_reserves=%s identity_exists=%s",
        row["id"],
        step,
        name,
        parent,
        create_reserves,
        fractional_identity_exists,
    )

    if step == 0:
        if fractional_identity_exists:
            _save_currency_state(conn, row["id"], status="in_progress", step_index=2, progress=progress)
            return

        logger.info("currency.fractional.rnc.submit request_id=%s name=%s parent=%s", row["id"], name, parent)
        logger.info(
            "currency.rpc.register_name_commitment.submit request_id=%s params=%s",
            row["id"],
            _safe_log_json(
                {
                    "name": name,
                    "control_address": payload["primary_raddress"],
                    "referral_id": "",
                    "parent": parent,
                    "source_of_funds": row["source_of_funds"],
                }
            ),
        )
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
        logger.info("currency.fractional.rnc.submitted request_id=%s txid=%s", row["id"], txid)
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
        if fractional_identity_exists:
            _save_currency_state(conn, row["id"], status="in_progress", step_index=2, progress=progress)
            return

        full_name = f"{name}.{parent}"
        identity_payload = _build_identity_payload(full_name, payload["primary_raddress"])
        fee_offer = _resolve_fee_offer(rpc, parent)
        logger.info(
            "currency.fractional.idr.submit request_id=%s full_name=%s fee_offer=%s source_of_funds=%s",
            row["id"],
            full_name,
            fee_offer,
            row["source_of_funds"],
        )
        logger.info(
            "currency.rpc.register_identity.submit request_id=%s full_name=%s params=%s",
            row["id"],
            full_name,
            _safe_log_json(
                {
                    "rnc_payload": progress.get("fractional_rnc_payload", {}),
                    "identity_payload": identity_payload,
                    "source_of_funds": row["source_of_funds"],
                    "fee_offer": fee_offer,
                }
            ),
        )
        txid = rpc.register_identity(
            progress.get("fractional_rnc_payload", {}),
            identity_payload,
            row["source_of_funds"],
            fee_offer,
        )
        if not isinstance(txid, str) or not txid:
            raise Exception("Fractional register_identity did not return txid")
        logger.info("currency.fractional.idr.submitted request_id=%s txid=%s", row["id"], txid)
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
        logger.info(
            "currency.fractional.step2.skip_direct_funding request_id=%s reason=%s",
            row["id"],
            "defer native+reserve funding to step4 after balance checks and batched sendcurrency planning",
        )
        _save_currency_state(
            conn,
            row["id"],
            status="in_progress",
            step_index=3,
            progress=progress,
            wait_type=None,
            wait_value=None,
        )
        return

    if step == 3:
        if not create_reserves or not payload.get("reserves"):
            _save_currency_state(conn, row["id"], status="in_progress", step_index=4, progress=progress)
            return
        _process_fractional_reserve_step(conn, row, payload, progress, rpc)
        return

    if step == 4:
        contributions = progress.setdefault("funded_initial_contributions", {})
        native = payload["native"]
        reserves = payload.get("reserves", [])
        target_identity = f"{name}@"
        pending_funding_waits = progress.setdefault("pending_funding_waits", [])

        # Resolve any previously submitted funding waits first, before planning new sends.
        if isinstance(pending_funding_waits, list) and pending_funding_waits:
            unresolved_waits: list[dict[str, str]] = []
            for wait in pending_funding_waits:
                if not isinstance(wait, dict):
                    continue
                wait_type = wait.get("wait_type")
                wait_value = wait.get("wait_value")
                if not isinstance(wait_type, str) or not isinstance(wait_value, str) or not wait_value:
                    continue

                if wait_type == "tx_confirm":
                    confirmations = get_tx_confirmations(rpc, wait_value)
                    if confirmations <= 0:
                        unresolved_waits.append({"wait_type": "tx_confirm", "wait_value": wait_value})
                    continue

                if wait_type == "opid_txid":
                    txid = poll_operation_for_txid(rpc, wait_value)
                    if not txid:
                        unresolved_waits.append({"wait_type": "opid_txid", "wait_value": wait_value})
                    else:
                        logger.info(
                            "currency.fractional.funding_wait_promoted request_id=%s opid=%s txid=%s",
                            row["id"],
                            wait_value,
                            txid,
                        )
                        unresolved_waits.append({"wait_type": "tx_confirm", "wait_value": txid})
                    continue

                unresolved_waits.append({"wait_type": wait_type, "wait_value": wait_value})

            progress["pending_funding_waits"] = unresolved_waits
            if unresolved_waits:
                logger.info(
                    "currency.fractional.step4.wait_funding_confirms request_id=%s pending_waits=%s",
                    row["id"],
                    len(unresolved_waits),
                )
                _save_currency_state(conn, row["id"], status="in_progress", step_index=4, progress=progress)
                return

        def _record_pending_wait(wait: tuple[str, str] | None):
            if wait is None:
                return
            wait_type, wait_value = wait
            logger.info(
                "currency.fractional.funding_wait_added request_id=%s wait_type=%s wait_value=%s",
                row["id"],
                wait_type,
                wait_value,
            )
            pending_funding_waits.append({"wait_type": wait_type, "wait_value": wait_value})

        # Step 4: plan all required top-ups first, then submit one sendcurrency call per source identity.
        native_initial_required = _normalize_amount(float(native["initial_contribution"]))
        define_funding_required = _normalize_amount(float(payload["define_funding_amount"]))
        native_total_required = _normalize_amount(native_initial_required + define_funding_required)
        contributions["native"] = native_initial_required
        reserve_contributions = contributions.setdefault("reserves", {})

        funding_plan: list[dict[str, Any]] = []

        _, native_shortfall = _compute_funding_shortfall(
            rpc,
            target_identity=target_identity,
            currency_name=native["name"],
            required_amount=native_total_required,
            source_identity=payload["primary_raddress"],
            context_hint="fractional identity native funding (define + initial)",
        )
        if native_shortfall > _amount_epsilon():
            funding_plan.append(
                {
                    "source": payload["primary_raddress"],
                    "currency": native["name"],
                    "address": target_identity,
                    "amount": native_shortfall,
                }
            )

        for reserve in reserves:
            reserve_name = reserve["name"]
            reserve_required = _normalize_amount(float(reserve["initial_contribution"]))
            reserve_contributions[reserve_name] = reserve_required
            reserve_source_identity = payload["allocation_id"] if create_reserves else payload["primary_raddress"]

            reserve_exists = _currency_exists(rpc, reserve_name)
            logger.info(
                "currency.fractional.reserve.precheck request_id=%s reserve=%s reserve_exists=%s create_reserves=%s source_identity=%s target_identity=%s requested=%s",
                row["id"],
                reserve_name,
                reserve_exists,
                create_reserves,
                reserve_source_identity,
                target_identity,
                reserve_required,
            )
            if not reserve_exists:
                mode_hint = "create_reserves=true expected prior reserve definecurrency step" if create_reserves else "create_reserves=false expects reserve currency to already exist"
                logger.error(
                    "currency.fractional.reserve.missing request_id=%s reserve=%s mode_hint=%s",
                    row["id"],
                    reserve_name,
                    mode_hint,
                )
                raise Exception(f"Reserve currency {reserve_name} not found; {mode_hint}")

            _, reserve_shortfall = _compute_funding_shortfall(
                rpc,
                target_identity=target_identity,
                currency_name=reserve_name,
                required_amount=reserve_required,
                source_identity=reserve_source_identity,
                context_hint=(
                    "reserve contribution after reserve creation"
                    if create_reserves
                    else "reserve contribution with pre-existing reserve currency"
                ),
            )
            if reserve_shortfall > _amount_epsilon():
                funding_plan.append(
                    {
                        "source": reserve_source_identity,
                        "currency": reserve_name,
                        "address": target_identity,
                        "amount": reserve_shortfall,
                    }
                )

        params_by_source: dict[str, list[dict[str, Any]]] = {}
        for item in funding_plan:
            params_by_source.setdefault(item["source"], []).append(
                {
                    "currency": item["currency"],
                    "address": item["address"],
                    "amount": item["amount"],
                }
            )

        if not funding_plan:
            logger.info(
                "currency.fractional.funding_plan_noop request_id=%s target_identity=%s reason=%s native_required=%s reserves=%s",
                row["id"],
                target_identity,
                "all required native and reserve contributions already funded",
                native_total_required,
                len(reserves),
            )

        funding_plan_summary = []
        for source_identity, params in params_by_source.items():
            total_amount = _normalize_amount(sum(float(item.get("amount", 0.0)) for item in params))
            currencies = [str(item.get("currency")) for item in params]
            funding_plan_summary.append(
                {
                    "source": source_identity,
                    "outputs": len(params),
                    "currencies": currencies,
                    "total_amount": total_amount,
                }
            )

        logger.info(
            "currency.fractional.funding_plan_summary request_id=%s target_identity=%s plan_entries=%s total_outputs=%s details=%s",
            row["id"],
            target_identity,
            len(params_by_source),
            len(funding_plan),
            _safe_log_json(funding_plan_summary),
        )

        for source_identity, params in params_by_source.items():
            waits = _submit_funding_transfers(rpc, source_identity, params)
            for wait in waits:
                _record_pending_wait(wait)

        progress["funded_initial_contributions"] = contributions
        _save_currency_state(conn, row["id"], status="in_progress", step_index=5, progress=progress)
        return

    if step == 5:
        native = payload["native"]
        reserves = payload.get("reserves", [])
        funded = progress.get("funded_initial_contributions", {})
        reserve_funded = funded.get("reserves", {}) if isinstance(funded.get("reserves"), dict) else {}
        pending_funding_waits = progress.get("pending_funding_waits", []) if isinstance(progress.get("pending_funding_waits", []), list) else []
        epsilon = _amount_epsilon()
        native_funded = float(funded.get("native", native["initial_contribution"]))
        initial_contributions = [_effective_contribution_amount(native_funded, apply_conversion_fee=True)]

        # Do not define until all previously submitted funding sends are confirmed.
        unresolved_waits: list[dict[str, str]] = []
        for wait in pending_funding_waits:
            if not isinstance(wait, dict):
                continue
            wait_type = wait.get("wait_type")
            wait_value = wait.get("wait_value")
            if not isinstance(wait_type, str) or not isinstance(wait_value, str) or not wait_value:
                continue

            if wait_type == "tx_confirm":
                confirmations = get_tx_confirmations(rpc, wait_value)
                if confirmations <= 0:
                    unresolved_waits.append({"wait_type": "tx_confirm", "wait_value": wait_value})
                continue

            if wait_type == "opid_txid":
                txid = poll_operation_for_txid(rpc, wait_value)
                if not txid:
                    unresolved_waits.append({"wait_type": "opid_txid", "wait_value": wait_value})
                else:
                    logger.info(
                        "currency.fractional.define.funding_wait_promoted request_id=%s opid=%s txid=%s",
                        row["id"],
                        wait_value,
                        txid,
                    )
                    unresolved_waits.append({"wait_type": "tx_confirm", "wait_value": txid})
                continue

            unresolved_waits.append({"wait_type": wait_type, "wait_value": wait_value})

        if unresolved_waits:
            progress["pending_funding_waits"] = unresolved_waits
            logger.info(
                "currency.fractional.define.wait_funding_confirms request_id=%s pending_waits=%s",
                row["id"],
                len(unresolved_waits),
            )
            _save_currency_state(conn, row["id"], status="in_progress", step_index=5, progress=progress)
            return

        progress["pending_funding_waits"] = []

        # Ensure full requested contributions are visible before fee-adjusting define payload.
        required_native_balance = _normalize_amount(float(payload["define_funding_amount"]) + native_funded)
        current_native_balance = _get_currency_balance_amount(rpc, f"{name}@", native["name"])
        if current_native_balance + epsilon < required_native_balance:
            logger.info(
                "currency.fractional.define.wait_native_funding request_id=%s identity=%s currency=%s required=%s current=%s",
                row["id"],
                f"{name}@",
                native["name"],
                required_native_balance,
                current_native_balance,
            )
            _save_currency_state(conn, row["id"], status="in_progress", step_index=5, progress=progress)
            return

        currencies = [native["name"]]
        weights = [native["weight"]]
        for reserve in reserves:
            currencies.append(reserve["name"])
            weights.append(reserve["weight"])
            reserve_required = float(reserve_funded.get(reserve["name"], reserve["initial_contribution"]))
            current_reserve_balance = _get_currency_balance_amount(rpc, f"{name}@", reserve["name"])
            if current_reserve_balance + epsilon < reserve_required:
                logger.info(
                    "currency.fractional.define.wait_reserve_funding request_id=%s identity=%s reserve=%s required=%s current=%s",
                    row["id"],
                    f"{name}@",
                    reserve["name"],
                    reserve_required,
                    current_reserve_balance,
                )
                _save_currency_state(conn, row["id"], status="in_progress", step_index=5, progress=progress)
                return
            initial_contributions.append(_effective_contribution_amount(reserve_required, apply_conversion_fee=True))

        # Sanity gate: require one additional sweep with balances still satisfied before define.
        sanity_signature = {
            "required_native_balance": required_native_balance,
            "native_currency": native["name"],
            "reserve_count": len(reserves),
        }
        previous_sanity_signature = progress.get("predefine_balance_sanity")
        if previous_sanity_signature != sanity_signature:
            progress["predefine_balance_sanity"] = sanity_signature
            logger.info(
                "currency.fractional.define.balance_sanity_pending request_id=%s signature=%s",
                row["id"],
                _safe_log_json(sanity_signature),
            )
            _save_currency_state(conn, row["id"], status="in_progress", step_index=5, progress=progress)
            return
        progress.pop("predefine_balance_sanity", None)

        logger.info(
            "currency.fractional.define.apply_conversion_fee request_id=%s fee=%s initial_contributions=%s",
            row["id"],
            _conversion_pc_fee(),
            _safe_log_json(initial_contributions),
        )

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

        logger.info(
            "currency.rpc.define_currency.submit request_id=%s params=%s",
            row["id"],
            _safe_log_json(options),
        )
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


def process_currency_once(
    *,
    get_db_connection: Callable[[], sqlite3.Connection],
    get_rpc_connection: Callable[[str], Any],
) -> int:
    conn = get_db_connection()
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
        logger.info(
            "currency.process.row request_id=%s workflow=%s status=%s step=%s wait_type=%s wait_value=%s attempts=%s",
            row["id"],
            row["workflow_type"],
            row_status,
            row["step_index"],
            row["wait_type"],
            row["wait_value"],
            row["attempts"],
        )
        try:
            rpc = get_rpc_connection(row["daemon_name"])

            if row_status == "waiting_confirm":
                txid = row["wait_value"]
                if not txid:
                    raise Exception("Missing wait txid while in waiting_confirm state")

                confirmations = get_tx_confirmations(rpc, txid)
                logger.info(
                    "currency.process.waiting_confirm request_id=%s txid=%s confirmations=%s",
                    row["id"],
                    txid,
                    confirmations,
                )
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

                resolved, next_wait_type, next_wait_value = resolve_wait_progress(rpc, "opid_txid", opid)
                logger.info(
                    "currency.process.waiting_opid request_id=%s opid=%s resolved=%s next_wait_type=%s next_wait_value=%s",
                    row["id"],
                    opid,
                    resolved,
                    next_wait_type,
                    next_wait_value,
                )
                if resolved and next_wait_type == "tx_confirm":
                    logger.info(
                        "currency.process.waiting_opid.promoted request_id=%s opid=%s txid=%s",
                        row["id"],
                        opid,
                        next_wait_value,
                    )
                    _save_currency_state(
                        conn,
                        row["id"],
                        status="waiting_confirm",
                        step_index=row["step_index"],
                        progress=_parse_json_or_empty(row["progress_json"]),
                        wait_type="tx_confirm",
                        wait_value=next_wait_value,
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
            logger.exception(
                "currency.process.error request_id=%s workflow=%s status=%s step=%s",
                row["id"],
                row["workflow_type"],
                row_status,
                row["step_index"],
            )
            logger.error(
                "currency.process.error.context request_id=%s payload=%s progress=%s",
                row["id"],
                _safe_log_json(_parse_json_or_empty(row["payload_json"])),
                _safe_log_json(_parse_json_or_empty(row["progress_json"])),
            )
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
