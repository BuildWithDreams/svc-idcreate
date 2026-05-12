from typing import Any


def get_operation_status_snapshot(rpc: Any, opid: str) -> dict[str, Any]:
    statuses = rpc.z_get_operation_status(opid)
    snapshot: dict[str, Any] = {
        "opid": opid,
        "entries": 0,
        "status": None,
        "has_result": False,
        "txid": None,
        "error": None,
    }

    if not isinstance(statuses, list) or not statuses:
        return snapshot

    snapshot["entries"] = len(statuses)
    first = statuses[0]
    if not isinstance(first, dict):
        return snapshot

    status = first.get("status")
    if isinstance(status, str):
        snapshot["status"] = status

    result = first.get("result")
    if isinstance(result, dict):
        snapshot["has_result"] = True
        txid = result.get("txid")
        if isinstance(txid, str) and txid:
            snapshot["txid"] = txid

    error = first.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        snapshot["error"] = message if isinstance(message, str) else str(error)
    elif isinstance(error, str):
        snapshot["error"] = error

    return snapshot


def get_tx_confirmations(rpc: Any, txid: str) -> int:
    tx = rpc.get_raw_transaction(txid)
    if isinstance(tx, dict):
        return int(tx.get("confirmations", 0) or 0)
    return 0


def poll_operation_for_txid(rpc: Any, opid: str) -> str | None:
    snapshot = get_operation_status_snapshot(rpc, opid)
    txid = snapshot.get("txid")
    return txid if isinstance(txid, str) and txid else None


def resolve_wait_progress(rpc: Any, wait_type: str, wait_value: str) -> tuple[bool, str, str]:
    """Resolve one worker wait state update.

    Returns (resolved, next_wait_type, next_wait_value).
    - For tx_confirm: resolved when confirmations > 0.
    - For opid_txid: resolves to tx_confirm once txid is available.
    """
    if wait_type == "tx_confirm":
        confirmations = get_tx_confirmations(rpc, wait_value)
        if confirmations > 0:
            return True, "", ""
        return False, "tx_confirm", wait_value

    if wait_type == "opid_txid":
        snapshot = get_operation_status_snapshot(rpc, wait_value)
        txid = snapshot.get("txid")
        if txid:
            return True, "tx_confirm", txid
        return False, "opid_txid", wait_value

    return False, wait_type, wait_value
