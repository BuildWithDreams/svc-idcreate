import sqlite3
import json
import os
import hmac
import hashlib
import logging
from datetime import datetime, timedelta, UTC
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib import request as urllib_request
from typing import Any

import id_create_service
from worker_currency_creation import process_currency_once as _process_currency_once_impl
from worker_id_creation import process_identity_once as _process_identity_once_impl


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


def _get_db_connection():
    return id_create_service._get_db_connection()


def _get_rpc_connection(daemon_name: str) -> Any:
    return id_create_service._get_rpc_connection(daemon_name)


def _retry_config() -> tuple[int, int]:
    max_retries = int(os.getenv("WORKER_MAX_RETRIES", "5"))
    base_seconds = int(os.getenv("WORKER_RETRY_BASE_SECONDS", "15"))
    return max_retries, base_seconds


def _next_retry_timestamp(delay_seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delay_seconds)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


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
    next_retry_at = _next_retry_timestamp(delay_seconds)
    conn.execute(
        """
        UPDATE registrations
        SET status = ?, attempts = ?, error_message = ?, next_retry_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, next_attempt, error, next_retry_at, row_id),
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
    next_retry_at = _next_retry_timestamp(delay_seconds)
    conn.execute(
        """
        UPDATE registrations
        SET webhook_attempts = ?, webhook_last_error = ?, webhook_next_retry_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (next_attempt, error, next_retry_at, row_id),
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
    def _normalize_fee_offer(value: float | int | str) -> float:
        dec_value = Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        return float(dec_value)

    def _resolve_encoded_import_fee_offer(currency: dict[str, Any], id_registration_fees: float | int) -> float | None:
        id_import_fees = currency.get("idimportfees")
        if id_import_fees is None:
            return None

        try:
            id_import_dec = Decimal(str(id_import_fees))
            scaled = id_import_dec * Decimal("100000000")
            index_dec = scaled.to_integral_value()
            if scaled != index_dec:
                return None
            index = int(index_dec)
        except (InvalidOperation, ValueError, TypeError):
            return None

        if index < 0 or index > 9:
            return None

        currencies = currency.get("currencies")
        if not isinstance(currencies, list) or index >= len(currencies):
            logger.warning(
                "Fee encoding index out of range parent=%s idimportfees=%s index=%s currencies_len=%s",
                parent_namespace,
                id_import_fees,
                index,
                len(currencies) if isinstance(currencies, list) else None,
            )
            return None

        reserve_currency_id = currencies[index]
        state = currency.get("lastconfirmedcurrencystate")
        if not isinstance(state, dict):
            state = currency.get("bestcurrencystate")
        reserves = state.get("reservecurrencies") if isinstance(state, dict) else None
        reserve_price = None
        if isinstance(reserves, list):
            for reserve in reserves:
                if isinstance(reserve, dict) and reserve.get("currencyid") == reserve_currency_id:
                    reserve_price = reserve.get("priceinreserve")
                    break

        if reserve_price is None:
            logger.warning(
                "Fee encoding reserve price missing parent=%s idimportfees=%s index=%s reserve_currency_id=%s",
                parent_namespace,
                id_import_fees,
                index,
                reserve_currency_id,
            )
            return None

        try:
            registration_fee_dec = Decimal(str(id_registration_fees))
            reserve_price_dec = Decimal(str(reserve_price))
            if reserve_price_dec <= 0:
                return None
            offer = registration_fee_dec / reserve_price_dec
            normalized_offer = Decimal(str(_normalize_fee_offer(offer)))
        except (InvalidOperation, ValueError, TypeError):
            return None

        currency_name = None
        names = currency.get("currencynames")
        if isinstance(names, dict):
            currency_name = names.get(reserve_currency_id)

        logger.info(
            "Resolved encoded namespace fee parent=%s idregistrationfees=%s idimportfees=%s index=%s reserve_currency_id=%s reserve_currency_name=%s priceinreserve=%s fee_offer_raw=%s fee_offer_normalized=%s",
            parent_namespace,
            id_registration_fees,
            id_import_fees,
            index,
            reserve_currency_id,
            currency_name,
            reserve_price,
            str(offer),
            str(normalized_offer),
        )
        return float(normalized_offer)

    fee_offer_env = os.getenv("FEE_OFFER", "").strip()
    if fee_offer_env:
        try:
            return _normalize_fee_offer(fee_offer_env)
        except ValueError:
            logger.warning("Invalid FEE_OFFER value=%s; falling back to currency idregistrationfees", fee_offer_env)

    try:
        currency = rpc.get_currency(parent_namespace)
        if isinstance(currency, dict) and currency.get("idregistrationfees") is not None:
            encoded_offer = _resolve_encoded_import_fee_offer(currency, currency["idregistrationfees"])
            if encoded_offer is not None:
                return encoded_offer
            return _normalize_fee_offer(currency["idregistrationfees"])
    except Exception as exc:
        logger.warning("Failed to resolve idregistrationfees for parent=%s error=%s", parent_namespace, exc)

    # Preserve historical behavior when dynamic fee lookup is unavailable.
    return 1


def process_currency_once() -> int:
    return _process_currency_once_impl(
        get_db_connection=_get_db_connection,
        get_rpc_connection=_get_rpc_connection,
    )


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
    next_retry_at = _next_retry_timestamp(delay_seconds)
    conn.execute(
        """
        UPDATE storage_uploads
        SET status = ?, attempts = ?, error_message = ?, next_retry_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, next_attempt, error, next_retry_at, upload_id),
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
                ORDER BY updated_at ASC
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
    updated_count = _process_identity_once_impl(
        get_db_connection=_get_db_connection,
        get_rpc_connection=_get_rpc_connection,
        to_log_dict=_to_log_dict,
        log_json=_log_json,
        record_retry_or_failure=_record_retry_or_failure,
        record_webhook_retry_or_failure=_record_webhook_retry_or_failure,
        build_identity_payload=_build_identity_payload,
        resolve_fee_offer=_resolve_fee_offer,
        post_webhook=_post_webhook,
        webhook_signature=_webhook_signature,
        logger=logger,
    )

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
