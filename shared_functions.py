import json
import os

from fastapi import HTTPException, status

from SFConstants import DAEMON_CONFIGS, VTRC_NATIVE_COINS


def log_json(data) -> str:
    try:
        return json.dumps(data, sort_keys=True, default=str)
    except Exception:
        return str(data)


def redact_fields(data: dict, redacted_keys: set[str] | None = None) -> dict:
    redacted = dict(data)
    if not redacted_keys:
        return redacted
    for key in redacted_keys:
        if key in redacted and redacted[key] is not None:
            redacted[key] = "***REDACTED***"
    return redacted


def mask_value(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "***MASKED***"
    return f"{value[:6]}...{value[-4:]}"


def normalize_parent_namespace(value: str) -> str:
    return value.strip().lower().rstrip("@").strip()


def canonicalize_parent_namespace(value: str) -> str:
    parent = value.strip().rstrip("@").strip()
    if not parent:
        raise ValueError("parent must not be empty")
    return f"{parent}@"


def build_identity_fqn(name: str, parent: str | None) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("name must not be empty")

    if parent is None or not parent.strip():
        return f"{normalized_name}@"

    normalized_parent = canonicalize_parent_namespace(parent)
    return f"{normalized_name}.{normalized_parent}"


def classify_getidentity_not_found(error: Exception) -> bool:
    args = getattr(error, "args", ())
    for arg in args:
        if isinstance(arg, dict):
            code = arg.get("code")
            if code == -5:
                return True
            message = str(arg.get("message", "")).lower()
            if "identity" in message and "not found" in message:
                return True

    lowered = str(error).lower()
    not_found_markers = (
        "identity not found",
        "error with get identity",
        "id not found",
    )
    return any(marker in lowered for marker in not_found_markers) and "not found" in lowered


def allowed_parent_namespaces() -> set[str]:
    configured: set[str] = set()

    for env_name in ("REGISTRAR_ALLOWED_PARENT", "PARENT"):
        value = os.getenv(env_name, "").strip()
        if value:
            configured.add(normalize_parent_namespace(value))

    raw_list = os.getenv("REGISTRAR_ALLOWED_PARENTS", "").strip()
    if raw_list:
        configured.update(normalize_parent_namespace(item) for item in raw_list.split(",") if item.strip())

    return configured


def resolve_daemon_by_native_coin(native_coin: str) -> str | None:
    requested_ticker = native_coin.strip().upper()
    for daemon_name, ticker in VTRC_NATIVE_COINS.items():
        if ticker.upper() == requested_ticker and daemon_name in DAEMON_CONFIGS:
            return daemon_name
    return None


def validate_currency_parent_or_403(parent: str):
    allowed_parents = allowed_parent_namespaces()
    parent_normalized = normalize_parent_namespace(parent)
    if allowed_parents and parent_normalized not in allowed_parents:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Requested parent namespace is not permitted.",
                "requested_parent": parent,
                "allowed_parents": sorted(allowed_parents),
            },
        )


def resolve_currency_daemon_or_503(native_coin: str) -> str:
    daemon_name = resolve_daemon_by_native_coin(native_coin)
    if daemon_name is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "native_coin": native_coin,
                "error": "No enabled daemon configured for requested native coin.",
            },
        )
    return daemon_name


def valid_api_keys() -> set[str]:
    raw_keys = os.getenv("REGISTRAR_API_KEYS", "")
    return {k.strip() for k in raw_keys.split(",") if k.strip()}


def require_api_key(api_key: str | None) -> str:
    valid_keys = valid_api_keys()
    if not valid_keys or api_key not in valid_keys:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return api_key
