"""
provisioning/router.py

FastAPI router for provisioning endpoints.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from provisioning.adapters import HttpProvisioningAdapter

# The provisioning engine (lazy-initialized to avoid import-order issues)
_engine: Optional["ProvisioningEngine"] = None
logger = logging.getLogger(__name__)


def _build_provisioning_adapter():
    mode = os.getenv("PROVISIONING_ADAPTER_MODE", "http").strip().lower()

    if mode == "http":
        base_url = os.getenv("PROVISIONING_SERVICE_URL", "").strip()
        if not base_url:
            raise RuntimeError("PROVISIONING_SERVICE_URL must be set when PROVISIONING_ADAPTER_MODE=http")
        timeout_seconds = int(os.getenv("PROVISIONING_HTTP_TIMEOUT_SECONDS", "10"))
        retry_count = int(os.getenv("PROVISIONING_RETRY_COUNT", "0"))
        logger.info(
            "using HTTP provisioning adapter base_url=%s timeout=%s retries=%s",
            base_url,
            timeout_seconds,
            retry_count,
        )
        return HttpProvisioningAdapter(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
        )

    raise RuntimeError(
        "Unsupported provisioning adapter mode. Set PROVISIONING_ADAPTER_MODE=http and PROVISIONING_SERVICE_URL."
    )


def get_engine(force_new: bool = False) -> "ProvisioningEngine":
    global _engine
    if _engine is None or force_new:
        from provisioning.engine import ProvisioningEngine

        _engine = ProvisioningEngine(
            signing_identity=os.getenv("SIGNING_IDENTITY", ""),
            signing_wif=os.getenv("PROVISIONING_SIGNING_WIF", ""),
            default_system_id=os.getenv("DEFAULT_SYSTEM_ID", ""),
            adapter=_build_provisioning_adapter(),
        )
        logger.info("provisioning engine initialized")
    return _engine


def _reset_engine() -> None:
    """Reset the cached engine. Used in tests to ensure clean state."""
    global _engine
    _engine = None


# ─── Request / Response Models ─────────────────────────────────────────────────

class ProvisioningChallengeRequest(BaseModel):
    name: str = Field(..., description="Identity name without parent namespace.", examples=["alice"])
    parent: str = Field(
        ..., description="Parent namespace (i-address or friendly name).", examples=["i84T3MWcb6zWcwgNZoU3TXtrUn9EqM84A4"]
    )
    primary_raddress: str = Field(
        ..., description="Primary R-address for identity control.", examples=["RExampleAddress123"]
    )
    system_id: Optional[str] = Field(
        default=None,
        description="System i-address (defaults to DEFAULT_SYSTEM_ID env var).",
        examples=["i7LaXD2cdy1ze33eHzZaEPyueT4yQmBfW"],
    )


class ProvisioningChallengeResponse(BaseModel):
    challenge_id: str
    name: str
    parent: str
    system_id: str
    primary_raddress: str
    deeplink_uri: str
    challenge_json: dict
    challenge_hex: str
    expires_at: int
    created_at: int


class ProvisioningRequestPayload(BaseModel):
    provisioning_request: dict = Field(
        ...,
        description="The wallet-returned ProvisioningRequest as a dict (JSON-serializable).",
    )


class ProvisioningResponseData(BaseModel):
    response_json: dict
    response_hex: str


class ProvisioningSubmitResponse(BaseModel):
    provisioning_response: ProvisioningResponseData
    request_id: str  # links to existing /api/status/{request_id}


class ProvisioningStatusResponse(BaseModel):
    challenge_id: str
    status: str  # pending | complete | failed
    name: Optional[str] = None
    parent: Optional[str] = None
    identity_address: Optional[str] = None
    fully_qualified_name: Optional[str] = None
    error_message: Optional[str] = None
    error_key: Optional[str] = None
    request_id: Optional[str] = None


# ─── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/provisioning", tags=["provisioning"])

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    valid_keys = {k.strip() for k in os.getenv("REGISTRAR_API_KEYS", "").split(",") if k.strip()}
    if not valid_keys or api_key not in valid_keys:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return api_key


# ─── POST /api/provisioning/challenge ──────────────────────────────────────────

@router.post(
    "/challenge",
    response_model=ProvisioningChallengeResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a provisioning challenge",
)
def create_provisioning_challenge(
    request: ProvisioningChallengeRequest,
    _api_key: str = Security(_require_api_key),
):
    """
    Create and sign a LoginConsentProvisioningChallenge.

    The challenge is stored in the engine's in-memory store (or can be
    swapped for Redis in production) and is valid for
    PROVISIONING_CHALLENGE_MAX_AGE_SECONDS (default 600s).

    The returned deeplink_uri can be encoded into a QR code or deep-linked
    to the user's wallet.
    """
    engine = get_engine()
    cleaned = engine.clear_expired_challenges()
    if cleaned:
        logger.info("expired challenges cleaned before challenge create count=%s", cleaned)

    try:
        result = engine.create_challenge(
            name=request.name,
            parent=request.parent,
            primary_raddress=request.primary_raddress,
            system_id=request.system_id,
        )
    except Exception as e:
        logger.exception("challenge create failed name=%s parent=%s", request.name, request.parent)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create challenge: {e}",
        )

    logger.info("challenge created challenge_id=%s", result["challenge_id"])

    return ProvisioningChallengeResponse(
        challenge_id=result["challenge_id"],
        name=result["name"],
        parent=result["parent"],
        system_id=result["system_id"],
        primary_raddress=result["primary_raddress"],
        deeplink_uri=result["deeplink_uri"],
        challenge_json=result["challenge_json"],
        challenge_hex=result["challenge_hex"],
        expires_at=result["expires_at"],
        created_at=result["created_at"],
    )


# ─── POST /api/provisioning/request ────────────────────────────────────────────

@router.post(
    "/request",
    response_model=ProvisioningSubmitResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit a wallet-signed provisioning request",
)
def submit_provisioning_request(
    payload: ProvisioningRequestPayload,
    _api_key: str = Security(_require_api_key),
):
    """
    Receive the wallet-signed ProvisioningRequest, verify it, execute the
    on-chain registration, and return a ProvisioningResponse.

    The request_id returned can be used with GET /api/status/{request_id}
    to poll for the registration result (mirrors the existing registration flow).
    """
    engine = get_engine()
    cleaned = engine.clear_expired_challenges()
    if cleaned:
        logger.info("expired challenges cleaned before request submit count=%s", cleaned)

    request_json = payload.provisioning_request

    try:
        parsed = engine.verify_and_parse_request(request_json)
    except Exception as e:
        logger.warning("request verify failed detail=%s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Extract challenge_id and prepare for registration
    challenge_id = parsed["challenge_id"]
    name = parsed["name"]
    parent = parsed["parent"]
    signing_address = parsed["signing_address"]
    primary_raddress = parsed["primary_raddress"]

    stored = engine.get_challenge_status(challenge_id)
    current_status = (stored or {}).get("status", "pending")
    if current_status != "pending":
        logger.warning(
            "replay blocked challenge_id=%s current_status=%s",
            challenge_id,
            current_status,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Challenge has already been consumed")

    engine.update_challenge_status(challenge_id, "request_received")
    logger.info("request accepted challenge_id=%s", challenge_id)

    # ── On-chain registration (reuse existing RPC logic) ──────────────────────
    # Get the registration DB row ID to return as request_id
    from id_create_service import (
        _get_rpc_connection,
        _get_db_connection,
    )
    import json

    daemon_name = "verusd_vrsc"  # TODO: resolve from parent/system_id
    source_of_funds = os.getenv("SOURCE_OF_FUNDS", "").strip()
    if not source_of_funds:
        raise HTTPException(status_code=503, detail="SOURCE_OF_FUNDS is not configured")

    try:
        rpc_conn = _get_rpc_connection(daemon_name)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RPC connection failed: {e}")

    try:
        rnc_response = rpc_conn.register_name_commitment(
            name,
            primary_raddress,
            "",
            parent,
            source_of_funds,
        )
    except Exception as e:
        engine.update_challenge_status(
            challenge_id,
            "failed",
            error_message=str(e),
            error_key="commitment",
        )
        logger.exception("rnc failed challenge_id=%s", challenge_id)
        # Build failure response
        resp = engine.build_failure_response(
            decision_id=challenge_id,
            signing_address=signing_address,
            error_key="commitment",
            error_desc=str(e),
            system_id=parsed.get("system_id", ""),
            request_json=request_json,
        )
        return ProvisioningSubmitResponse(
            provisioning_response=ProvisioningResponseData(**resp),
            request_id="",
        )

    # Persist to DB for worker to pick up
    request_id = str(uuid.uuid4())
    conn = _get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO registrations (
                id, requested_name, parent_namespace, native_coin, daemon_name,
                primary_raddress, source_of_funds, status,
                rnc_txid, rnc_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                name,
                parent,
                "VRSC",  # native_coin — TODO: resolve from system_id
                daemon_name,
                primary_raddress,
                source_of_funds,
                "pending_rnc_confirm",
                rnc_response.get("txid"),
                json.dumps(rnc_response),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    engine.update_challenge_status(challenge_id, "submitted", request_id=request_id)
    logger.info("registration queued challenge_id=%s request_id=%s", challenge_id, request_id)

    # Build provisional success response (status will be updated by worker)
    # Note: in a full implementation, the response is sent after worker confirms.
    # For now, we return immediately with the request_id for polling.
    def _strip_nones(value):
        if isinstance(value, dict):
            return {k: _strip_nones(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [_strip_nones(v) for v in value]
        return value

    response_request_json = _strip_nones(dict(request_json))
    if not response_request_json.get("system_id"):
        response_request_json["system_id"] = parsed.get("system_id")
    if not response_request_json.get("signing_id"):
        response_request_json["signing_id"] = parsed.get("signing_id") or parsed.get("system_id")

    resp = engine.build_success_response(
        decision_id=challenge_id,
        signing_address=signing_address,
        identity_address="",  # filled by worker once ID is created
        fully_qualified_name=f"{name}.{parent}@",
        system_id=parsed.get("system_id", ""),
        parent=parent,
        txids=[rnc_response.get("txid", "")],
        request_json=response_request_json,
    )

    return ProvisioningSubmitResponse(
        provisioning_response=ProvisioningResponseData(**resp),
        request_id=request_id,
    )


# ─── GET /api/provisioning/status/{challenge_id} ────────────────────────────────

@router.get(
    "/status/{challenge_id}",
    response_model=ProvisioningStatusResponse,
    summary="Get provisioning status by challenge ID",
)
def get_provisioning_status(challenge_id: str):
    """
    Return the current provisioning state for a challenge_id.

    The challenge_id is the decision_id returned by the challenge creation
    or embedded in the wallet's ProvisioningRequest.

    Challenge state is stored durably in SQLite via the ProvisioningEngine,
    with in-memory cache used as a fast path.
    """
    engine = get_engine()
    cleaned = engine.clear_expired_challenges()
    if cleaned:
        logger.info("expired challenges cleaned before status read count=%s", cleaned)

    stored = engine.get_challenge_status(challenge_id)
    if stored is None:
        logger.warning("status not found challenge_id=%s", challenge_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    return ProvisioningStatusResponse(
        challenge_id=challenge_id,
        status=stored.get("status", "pending"),
        name=stored.get("name"),
        parent=stored.get("parent"),
        identity_address=stored.get("identity_address"),
        fully_qualified_name=stored.get("fully_qualified_name"),
        error_message=stored.get("error_message"),
        error_key=stored.get("error_key"),
        request_id=stored.get("request_id"),
    )
