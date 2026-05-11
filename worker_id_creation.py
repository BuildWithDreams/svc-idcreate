import json
import os
import sqlite3
from typing import Any, Callable


def process_identity_once(
    *,
    get_db_connection: Callable[[], sqlite3.Connection],
    get_rpc_connection: Callable[[str], Any],
    to_log_dict: Callable[..., dict],
    log_json: Callable[[Any], str],
    record_retry_or_failure: Callable[[sqlite3.Connection, str, int, str, str], None],
    record_webhook_retry_or_failure: Callable[[sqlite3.Connection, str, int, str], None],
    build_identity_payload: Callable[[str, str], dict],
    resolve_fee_offer: Callable[[Any, str], float | int],
    post_webhook: Callable[[str, dict, dict, int], None],
    webhook_signature: Callable[[str, dict], str],
    logger: Any,
) -> int:
    logger.info("worker.process_once.start")
    conn = get_db_connection()
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
            log_json(to_log_dict(row)),
        )
        try:
            rpc = get_rpc_connection(row["daemon_name"])
            tx = rpc.get_raw_transaction(row["rnc_txid"])
            confirmations = 0
            if isinstance(tx, dict):
                confirmations = tx.get("confirmations", 0)
            logger.debug(
                "worker.process_once.pending_rnc_confirm.rpc_response request_id=%s rnc_txid=%s confirmations=%s tx=%s",
                row["id"],
                row["rnc_txid"],
                confirmations,
                log_json(tx),
            )
        except Exception as exc:
            logger.exception(
                "worker.process_once.pending_rnc_confirm.rpc_error request_id=%s daemon=%s rnc_txid=%s",
                row["id"],
                row["daemon_name"],
                row["rnc_txid"],
            )
            record_retry_or_failure(conn, row["id"], row["attempts"], str(exc), "pending_rnc_confirm")
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
            log_json(to_log_dict(row)),
        )
        rpc = get_rpc_connection(row["daemon_name"])
        rnc_payload = json.loads(row["rnc_payload_json"])
        full_name = f'{row["requested_name"]}.{row["parent_namespace"]}'
        identity_payload = build_identity_payload(full_name, row["primary_raddress"])
        fee_offer = resolve_fee_offer(rpc, row["parent_namespace"])
        logger.debug(
            "worker.process_once.ready_for_idr.computed request_id=%s full_name=%s rnc_payload=%s identity_payload=%s fee_offer=%s",
            row["id"],
            full_name,
            log_json(rnc_payload),
            log_json(identity_payload),
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
            record_retry_or_failure(conn, row["id"], row["attempts"], str(exc), "ready_for_idr")
        updated_count += 1

    for row in submitted_rows:
        logger.debug(
            "worker.process_once.idr_submitted.check row=%s",
            log_json(to_log_dict(row)),
        )
        try:
            rpc = get_rpc_connection(row["daemon_name"])
            tx = rpc.get_raw_transaction(row["idr_txid"])
            confirmations = 0
            if isinstance(tx, dict):
                confirmations = tx.get("confirmations", 0)
            logger.debug(
                "worker.process_once.idr_submitted.rpc_response request_id=%s idr_txid=%s confirmations=%s tx=%s",
                row["id"],
                row["idr_txid"],
                confirmations,
                log_json(tx),
            )
        except Exception as exc:
            logger.exception(
                "worker.process_once.idr_submitted.rpc_error request_id=%s daemon=%s idr_txid=%s",
                row["id"],
                row["daemon_name"],
                row["idr_txid"],
            )
            record_retry_or_failure(conn, row["id"], row["attempts"], str(exc), "idr_submitted")
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
            log_json(to_log_dict(row, redacted_keys={"webhook_secret"})),
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
            headers["X-Webhook-Signature"] = webhook_signature(secret, payload)

        logger.debug(
            "worker.process_once.webhook.prepared request_id=%s payload=%s headers=%s timeout_seconds=%s",
            row["id"],
            log_json(payload),
            log_json(
                {
                    "Content-Type": headers.get("Content-Type"),
                    "X-Webhook-Event": headers.get("X-Webhook-Event"),
                    "has_signature": "X-Webhook-Signature" in headers,
                }
            ),
            webhook_timeout_seconds,
        )

        try:
            post_webhook(row["webhook_url"], payload, headers, webhook_timeout_seconds)
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
            record_webhook_retry_or_failure(conn, row["id"], row["webhook_attempts"], str(exc))
        updated_count += 1

    logger.info("worker.process_once.commit updated_count=%s", updated_count)
    conn.commit()
    conn.close()
    return updated_count
