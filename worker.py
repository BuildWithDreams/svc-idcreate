import sqlite3
import json
import os
import hmac
import hashlib
import logging
from urllib import request as urllib_request
from typing import Any

import id_create_service


logger = logging.getLogger(__name__)


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

    updated_count = 0
    for row in pending_rows:
        try:
            rpc = _get_rpc_connection(row["daemon_name"])
            tx = rpc.get_raw_transaction(row["rnc_txid"])
            confirmations = 0
            if isinstance(tx, dict):
                confirmations = tx.get("confirmations", 0)
        except Exception as exc:
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
            updated_count += 1

    for row in ready_rows:
        rpc = _get_rpc_connection(row["daemon_name"])
        rnc_payload = json.loads(row["rnc_payload_json"])
        full_name = f'{row["requested_name"]}.{row["parent_namespace"]}'
        identity_payload = _build_identity_payload(full_name, row["primary_raddress"])
        fee_offer = _resolve_fee_offer(rpc, row["parent_namespace"])

        try:
            txid = rpc.register_identity(
                rnc_payload,
                identity_payload,
                row["source_of_funds"],
                fee_offer,
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
            _record_retry_or_failure(conn, row["id"], row["attempts"], str(exc), "ready_for_idr")
        updated_count += 1

    for row in submitted_rows:
        try:
            rpc = _get_rpc_connection(row["daemon_name"])
            tx = rpc.get_raw_transaction(row["idr_txid"])
            confirmations = 0
            if isinstance(tx, dict):
                confirmations = tx.get("confirmations", 0)
        except Exception as exc:
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
            updated_count += 1

    webhook_timeout_seconds = int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "5"))
    fallback_secret = os.getenv("WEBHOOK_SIGNING_SECRET", "")

    for row in webhook_rows:
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
        except Exception as exc:
            _record_webhook_retry_or_failure(conn, row["id"], row["webhook_attempts"], str(exc))
        updated_count += 1

    conn.commit()
    conn.close()

    storage_updated_count = process_storage_once()
    return updated_count + storage_updated_count


if __name__ == "__main__":
    advanced = process_once()
    print(f"Advanced rows: {advanced}")
