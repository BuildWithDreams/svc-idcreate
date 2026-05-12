from typing import Any


def get_tx_confirmations(rpc: Any, txid: str) -> int:
    tx = rpc.get_raw_transaction(txid)
    if isinstance(tx, dict):
        return int(tx.get("confirmations", 0) or 0)
    return 0


def poll_operation_for_txid(rpc: Any, opid: str) -> str | None:
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
        txid = poll_operation_for_txid(rpc, wait_value)
        if txid:
            return True, "tx_confirm", txid
        return False, "opid_txid", wait_value

    return False, wait_type, wait_value
