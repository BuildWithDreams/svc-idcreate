from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Security
from fastapi import status
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
import os
import json
import sqlite3
import uuid
import logging
import math
import hashlib
import re
from SFConstants import DAEMON_CONFIGS, DAEMON_VERUSD_VRSC, VTRC_NATIVE_COINS

# Provisioning endpoints
from provisioning.router import router as provisioning_router

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

logging.basicConfig(
    level=getattr(logging, os.getenv("PROVISIONING_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    name: str = Field(description="Identity name without parent namespace.", examples=["alice"])
    parent: str = Field(description="Parent namespace/currency name.", examples=["bitcoins.vrsc"])
    native_coin: str = Field(description="Ticker used to resolve enabled daemon.", examples=["VRSC"])
    primary_raddress: str = Field(description="Primary R-address for identity control.", examples=["RaliceAddress"])
    webhook_url: str | None = Field(
        default=None,
        description="Optional callback URL notified when request reaches complete/failed status.",
        examples=["https://example.com/hook"],
    )
    webhook_secret: str | None = Field(
        default=None,
        description="Optional per-request signing secret for webhook payload HMAC.",
        examples=["my-webhook-secret"],
    )


class StorageUploadRequest(BaseModel):
    name: str
    parent: str
    native_coin: str
    primary_raddress: str
    file_path: str
    mime_type: str | None = "application/octet-stream"
    chunk_size_bytes: int = 999000


def _get_db_path() -> str:
    return os.getenv("REGISTRAR_DB_PATH", "registrar.db")


def _get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _create_storage_upload_record(record: dict) -> None:
    conn = _get_db_connection()
    conn.execute(
        """
        INSERT INTO storage_uploads (
            id,
            requested_name,
            parent_namespace,
            identity_fqn,
            native_coin,
            daemon_name,
            status,
            file_path,
            mime_type,
            file_size,
            sha256_hex,
            chunk_size_bytes,
            chunk_count,
            current_chunk_index,
            attempts,
            next_retry_at,
            error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["id"],
            record["requested_name"],
            record["parent_namespace"],
            record["identity_fqn"],
            record["native_coin"],
            record["daemon_name"],
            record.get("status", "pending"),
            record["file_path"],
            record.get("mime_type"),
            record["file_size"],
            record["sha256_hex"],
            record.get("chunk_size_bytes", 999000),
            record["chunk_count"],
            record.get("current_chunk_index", 0),
            record.get("attempts", 0),
            record.get("next_retry_at"),
            record.get("error_message"),
        ),
    )
    conn.commit()
    conn.close()


def _create_storage_chunk_record(record: dict) -> None:
    conn = _get_db_connection()
    conn.execute(
        """
        INSERT INTO storage_chunks (
            upload_id,
            chunk_index,
            vdxf_key,
            txid,
            status,
            label,
            ivk,
            epk,
            objectdata_ref_json,
            error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["upload_id"],
            record["chunk_index"],
            record["vdxf_key"],
            record.get("txid"),
            record.get("status", "pending"),
            record.get("label"),
            record.get("ivk"),
            record.get("epk"),
            record.get("objectdata_ref_json"),
            record.get("error_message"),
        ),
    )
    conn.commit()
    conn.close()


def _get_storage_upload_record(upload_id: str) -> dict | None:
    conn = _get_db_connection()
    row = conn.execute("SELECT * FROM storage_uploads WHERE id = ?", (upload_id,)).fetchone()
    conn.close()
    return dict(row) if row is not None else None


def _list_storage_chunk_records(upload_id: str) -> list[dict]:
    conn = _get_db_connection()
    rows = conn.execute(
        "SELECT * FROM storage_chunks WHERE upload_id = ? ORDER BY chunk_index ASC",
        (upload_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _storage_allowed_base_dir() -> str:
    configured = os.getenv("STORAGE_ALLOWED_BASE_DIR", "").strip()
    return configured if configured else os.getcwd()


def _is_path_allowed(file_path: str, allowed_base_dir: str) -> bool:
    try:
        file_real = os.path.realpath(file_path)
        base_real = os.path.realpath(allowed_base_dir)
        return os.path.commonpath([file_real, base_real]) == base_real
    except Exception:
        return False


def _sha256_file(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _chunk_bytes_from_rpc(rpc_connection, identity_fqn: str, chunk_row: dict) -> bytes:
    content = rpc_connection.get_identity_content(
        identity_name_or_id=identity_fqn,
        vdxf_key=chunk_row["vdxf_key"],
    )

    descriptor = {}
    items = content.get("items") if isinstance(content, dict) else None
    if isinstance(items, list) and items:
        first_item = items[0]
        if isinstance(first_item, dict):
            candidate = first_item.get("datadescriptor")
            if isinstance(candidate, dict):
                descriptor = candidate

    decrypt_payload = {
        "datadescriptor": descriptor,
        "txid": chunk_row.get("txid"),
        "retrieve": True,
    }
    decrypted = rpc_connection.decrypt_data(decrypt_payload)
    if not isinstance(decrypted, list) or not decrypted:
        raise HTTPException(status_code=503, detail="Failed to retrieve chunk data")

    objectdata_hex = decrypted[0].get("objectdata", "")
    try:
        return bytes.fromhex(objectdata_hex)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Invalid chunk objectdata hex: {e}")


def _init_db():
    conn = _get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registrations (
            id TEXT PRIMARY KEY,
            requested_name TEXT NOT NULL,
            parent_namespace TEXT NOT NULL,
            native_coin TEXT NOT NULL,
            daemon_name TEXT NOT NULL,
            primary_raddress TEXT NOT NULL,
            source_of_funds TEXT NOT NULL,
            status TEXT NOT NULL,
            rnc_txid TEXT,
            rnc_payload_json TEXT,
            idr_txid TEXT,
            error_message TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at TIMESTAMP,
            webhook_url TEXT,
            webhook_secret TEXT,
            webhook_delivered INTEGER NOT NULL DEFAULT 0,
            webhook_attempts INTEGER NOT NULL DEFAULT 0,
            webhook_last_error TEXT,
            webhook_next_retry_at TIMESTAMP,
            webhook_delivered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(registrations)").fetchall()
    }
    if "idr_txid" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN idr_txid TEXT")
    if "attempts" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    if "next_retry_at" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN next_retry_at TIMESTAMP")
    if "webhook_url" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN webhook_url TEXT")
    if "webhook_secret" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN webhook_secret TEXT")
    if "webhook_delivered" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN webhook_delivered INTEGER NOT NULL DEFAULT 0")
    if "webhook_attempts" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN webhook_attempts INTEGER NOT NULL DEFAULT 0")
    if "webhook_last_error" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN webhook_last_error TEXT")
    if "webhook_next_retry_at" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN webhook_next_retry_at TIMESTAMP")
    if "webhook_delivered_at" not in columns:
        conn.execute("ALTER TABLE registrations ADD COLUMN webhook_delivered_at TIMESTAMP")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_registrations_status ON registrations(status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS storage_uploads (
            id TEXT PRIMARY KEY,
            requested_name TEXT NOT NULL,
            parent_namespace TEXT NOT NULL,
            identity_fqn TEXT NOT NULL,
            native_coin TEXT NOT NULL,
            daemon_name TEXT NOT NULL,
            status TEXT NOT NULL,
            file_path TEXT NOT NULL,
            mime_type TEXT,
            file_size INTEGER NOT NULL,
            sha256_hex TEXT NOT NULL,
            chunk_size_bytes INTEGER NOT NULL DEFAULT 999000,
            chunk_count INTEGER NOT NULL,
            current_chunk_index INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at TIMESTAMP,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS storage_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            vdxf_key TEXT NOT NULL,
            txid TEXT,
            status TEXT NOT NULL,
            label TEXT,
            ivk TEXT,
            epk TEXT,
            objectdata_ref_json TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(upload_id, chunk_index)
        )
        """
    )
    storage_upload_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(storage_uploads)").fetchall()
    }
    if "attempts" not in storage_upload_columns:
        conn.execute("ALTER TABLE storage_uploads ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    if "next_retry_at" not in storage_upload_columns:
        conn.execute("ALTER TABLE storage_uploads ADD COLUMN next_retry_at TIMESTAMP")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_storage_uploads_status ON storage_uploads(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_storage_chunks_upload_id ON storage_chunks(upload_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_storage_chunks_status ON storage_chunks(status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT,
            signature TEXT,
            payload_json TEXT NOT NULL,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_webhook_events_received_at ON webhook_events(received_at)")

    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_db()
    yield


app = FastAPI(lifespan=lifespan)

# Mount provisioning endpoints (/api/provisioning/*)
app.include_router(provisioning_router)


def _valid_api_keys() -> set[str]:
    raw_keys = os.getenv("REGISTRAR_API_KEYS", "")
    return {k.strip() for k in raw_keys.split(",") if k.strip()}


def _require_api_key(api_key: str | None = Security(api_key_header)) -> str:
    valid_keys = _valid_api_keys()
    if not valid_keys or api_key not in valid_keys:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return api_key


def _get_rpc_connection(daemon_name: str):
    from rpc_manager import VerusRPCManager

    return VerusRPCManager.get_connection(daemon_name)


def _resolve_daemon_by_native_coin(native_coin: str) -> str | None:
    requested_ticker = native_coin.strip().upper()
    for daemon_name, ticker in VTRC_NATIVE_COINS.items():
        if ticker.upper() == requested_ticker and daemon_name in DAEMON_CONFIGS:
            return daemon_name
    return None


def _allowed_parent_namespaces() -> set[str]:
    configured: set[str] = set()

    # Backward-compatible single preset parent.
    for env_name in ("REGISTRAR_ALLOWED_PARENT", "PARENT"):
        value = os.getenv(env_name, "").strip()
        if value:
            configured.add(value.lower())

    # Preferred comma-separated allowlist.
    raw_list = os.getenv("REGISTRAR_ALLOWED_PARENTS", "").strip()
    if raw_list:
        configured.update(item.strip().lower() for item in raw_list.split(",") if item.strip())

    return configured


def _store_webhook_event(event_name: str | None, signature: str | None, payload: dict | list | str) -> None:
    payload_json = json.dumps(payload)
    conn = _get_db_connection()
    conn.execute(
        """
        INSERT INTO webhook_events (event_name, signature, payload_json)
        VALUES (?, ?, ?)
        """,
        (event_name, signature, payload_json),
    )
    conn.commit()
    conn.close()


def _list_webhook_events(limit: int = 20) -> list[dict]:
    conn = _get_db_connection()
    rows = conn.execute(
        """
        SELECT id, event_name, signature, payload_json, received_at
        FROM webhook_events
        ORDER BY datetime(received_at) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _select_storage_mode(payload_size_bytes: int) -> str:
    safe_raw_limit = int(os.getenv("STORAGE_RAW_SAFE_LIMIT_BYTES", "4096"))
    return "raw_contentmultimap" if payload_size_bytes <= safe_raw_limit else "identity_data_wrapper"


def _build_storage_contentmultimap_entry(
    vdxf_key: str,
    identity_address: str,
    filename: str,
    label: str | None = None,
    mimetype: str = "application/octet-stream",
    create_mmr: bool = True,
) -> dict:
    data_object = {
        "address": identity_address,
        "filename": filename,
        "createmmr": create_mmr,
        "mimetype": mimetype,
    }
    if label is not None:
        data_object["label"] = label

    return {
        vdxf_key: [
            {
                "data": data_object,
            }
        ]
    }


def _normalize_namespace_slug(raw_namespace: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", raw_namespace.strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise ValueError("namespace must contain at least one alphanumeric character")
    return slug


def _build_namespace_key_map(namespace: str, page_count: int) -> dict:
    if page_count < 1:
        raise ValueError("page_count must be at least 1")

    ns = _normalize_namespace_slug(namespace)
    return {
        "namespace": ns,
        "manifest_name": f"{ns}::manifest",
        "tx_map_name": f"{ns}::tx_map",
        "page_names": [f"{ns}::page.{idx}" for idx in range(page_count)],
    }


def _interrogate_daemon_health(daemon_name: str, requested_native_coin: str | None = None):
    cfg = DAEMON_CONFIGS.get(daemon_name, {})
    logger.info(
        "health check start daemon=%s native_coin=%s configured=%s host=%s port=%s user=%s",
        daemon_name,
        requested_native_coin,
        daemon_name in DAEMON_CONFIGS,
        cfg.get("host"),
        cfg.get("port"),
        cfg.get("user"),
    )
    try:
        connection = _get_rpc_connection(daemon_name)
        logger.info("health rpc connection acquired daemon=%s connection_type=%s", daemon_name, type(connection).__name__)
        rpc_info = connection.get_info()
        logger.info("health rpc getinfo succeeded daemon=%s", daemon_name)
    except Exception as e:
        logger.exception(
            "health rpc check failed daemon=%s native_coin=%s error_type=%s",
            daemon_name,
            requested_native_coin,
            type(e).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "version": os.getenv("IMAGE_TAG", "local"),
                "daemon": daemon_name,
                "native_coin": requested_native_coin,
                "error": str(e),
            },
        )

    return {
        "status": "ok",
        "version": os.getenv("IMAGE_TAG", "local"),
        "daemon": daemon_name,
        "native_coin": requested_native_coin,
        "rpc": {
            "reachable": True,
            "info": rpc_info,
        },
    }

# 1. Health Check (Crucial for Docker/K8s)
@app.get("/health")
def health_check(
    native_coin: str | None = Query(
        default=None,
        description="Native coin ticker to resolve daemon health (e.g. VRSC, VARRR, VDEX, CHIPS).",
    )
):
    """
    Check service and daemon RPC health.

    When `native_coin` is provided, the endpoint resolves the daemon by ticker
    from configured native coin mappings and only considers enabled daemons.
    If no enabled daemon matches, returns HTTP 503.

    When `native_coin` is omitted, the endpoint checks the daemon defined by
    `HEALTH_RPC_DAEMON` (default: `verusd_vrsc`).
    """
    if native_coin:
        daemon_name = _resolve_daemon_by_native_coin(native_coin)
        if daemon_name is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "degraded",
                    "version": os.getenv("IMAGE_TAG", "local"),
                    "native_coin": native_coin,
                    "error": "No enabled daemon configured for requested native coin.",
                },
            )
        return _interrogate_daemon_health(daemon_name, requested_native_coin=native_coin)

    daemon_name = os.getenv("HEALTH_RPC_DAEMON", DAEMON_VERUSD_VRSC)
    return _interrogate_daemon_health(daemon_name)


@app.post("/api/register", status_code=202, summary="Start asynchronous ID registration")
def register_identity(request: RegisterRequest, api_key: str = Security(_require_api_key)):
    """
    Register name commitment and persist request state for async completion.

    The endpoint returns immediately with a request id after broadcasting the
    name commitment transaction and storing the request in SQLite.
    """
    daemon_name = _resolve_daemon_by_native_coin(request.native_coin)
    if daemon_name is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "native_coin": request.native_coin,
                "error": "No enabled daemon configured for requested native coin.",
            },
        )

    source_of_funds = os.getenv("SOURCE_OF_FUNDS", "").strip()
    if not source_of_funds:
        raise HTTPException(status_code=503, detail="SOURCE_OF_FUNDS is not configured")

    allowed_parents = _allowed_parent_namespaces()
    parent_normalized = request.parent.strip().lower()
    if allowed_parents and parent_normalized not in allowed_parents:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Requested parent namespace is not permitted.",
                "requested_parent": request.parent,
                "allowed_parents": sorted(allowed_parents),
            },
        )

    try:
        rpc_connection = _get_rpc_connection(daemon_name)
        rnc_response = rpc_connection.register_name_commitment(
            request.name,
            request.primary_raddress,
            "",
            request.parent,
            source_of_funds,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RPC error during name commitment: {e}")

    request_id = str(uuid.uuid4())
    conn = _get_db_connection()
    conn.execute(
        """
        INSERT INTO registrations (
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            primary_raddress,
            source_of_funds,
            status,
            rnc_txid,
            rnc_payload_json,
            webhook_url,
            webhook_secret
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            request.name,
            request.parent,
            request.native_coin,
            daemon_name,
            request.primary_raddress,
            source_of_funds,
            "pending_rnc_confirm",
            rnc_response.get("txid"),
            json.dumps(rnc_response),
            request.webhook_url,
            request.webhook_secret,
        ),
    )
    conn.commit()
    conn.close()

    return {
        "request_id": request_id,
        "status": "pending_rnc_confirm",
        "daemon": daemon_name,
        "native_coin": request.native_coin,
        "txid_rnc": rnc_response.get("txid"),
    }


@app.get("/api/status/{request_id}", summary="Get registration request status")
def get_registration_status(request_id: str):
    """
    Return persisted registration state by request id.

    Includes lifecycle status, txids, retry fields, and webhook delivery state.
    """
    conn = _get_db_connection()
    row = conn.execute("SELECT * FROM registrations WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")

    data = dict(row)
    if data.get("rnc_payload_json"):
        data["rnc_payload"] = json.loads(data["rnc_payload_json"])
    return data


@app.post("/api/webhook/requeue/{request_id}", summary="Requeue webhook delivery for a terminal request")
def requeue_webhook_delivery(request_id: str, api_key: str = Security(_require_api_key)):
    """
    Reset webhook delivery fields so worker can retry webhook dispatch.

    Only allowed when request is in terminal status (`complete` or `failed`).
    """
    conn = _get_db_connection()
    row = conn.execute(
        "SELECT id, status, webhook_url FROM registrations WHERE id = ?",
        (request_id,),
    ).fetchone()

    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Request not found")

    if row["status"] not in {"complete", "failed"}:
        conn.close()
        raise HTTPException(status_code=409, detail="Webhook can only be requeued for terminal requests")

    if not row["webhook_url"]:
        conn.close()
        raise HTTPException(status_code=409, detail="Request has no webhook_url configured")

    conn.execute(
        """
        UPDATE registrations
        SET webhook_delivered = 0,
            webhook_attempts = 0,
            webhook_last_error = NULL,
            webhook_next_retry_at = NULL,
            webhook_delivered_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (request_id,),
    )
    conn.commit()
    conn.close()

    return {
        "request_id": request_id,
        "status": "queued",
        "message": "Webhook delivery has been requeued.",
    }


@app.get("/api/registrations/failures", summary="List recent failed registrations for operations")
def list_recent_failures(
    limit: int = Query(default=20, ge=1, le=200, description="Maximum number of failed requests to return."),
    api_key: str = Security(_require_api_key),
):
    """Return recent failed registration requests ordered by last update time descending."""
    conn = _get_db_connection()
    rows = conn.execute(
        """
        SELECT
            id,
            requested_name,
            parent_namespace,
            native_coin,
            daemon_name,
            status,
            error_message,
            attempts,
            next_retry_at,
            webhook_url,
            webhook_delivered,
            webhook_attempts,
            webhook_last_error,
            updated_at,
            created_at
        FROM registrations
        WHERE status = 'failed'
        ORDER BY datetime(updated_at) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    items = [dict(r) for r in rows]
    return {
        "count": len(items),
        "items": items,
    }


@app.post("/api/storage/upload", status_code=202, summary="Create storage upload request")
def create_storage_upload(request: StorageUploadRequest, api_key: str = Security(_require_api_key)):
    allowed_base_dir = _storage_allowed_base_dir()
    if not os.path.isfile(request.file_path):
        raise HTTPException(status_code=400, detail="file_path does not exist or is not a file")
    if not _is_path_allowed(request.file_path, allowed_base_dir):
        raise HTTPException(status_code=400, detail="file_path is outside allowed storage base directory")

    max_upload_bytes = int(os.getenv("STORAGE_MAX_UPLOAD_BYTES", "20000000"))
    file_size = os.path.getsize(request.file_path)
    if file_size > max_upload_bytes:
        raise HTTPException(status_code=400, detail="file exceeds STORAGE_MAX_UPLOAD_BYTES")

    chunk_size = max(1, min(request.chunk_size_bytes, 999000))
    chunk_count = max(1, math.ceil(file_size / chunk_size))
    upload_id = str(uuid.uuid4())

    identity_fqn = f"{request.name}.{request.parent}@"
    upload_record = {
        "id": upload_id,
        "requested_name": request.name,
        "parent_namespace": request.parent,
        "identity_fqn": identity_fqn,
        "native_coin": request.native_coin,
        "daemon_name": _resolve_daemon_by_native_coin(request.native_coin) or "unresolved",
        "status": "pending",
        "file_path": request.file_path,
        "mime_type": request.mime_type,
        "file_size": file_size,
        "sha256_hex": _sha256_file(request.file_path),
        "chunk_size_bytes": chunk_size,
        "chunk_count": chunk_count,
    }
    _create_storage_upload_record(upload_record)

    for idx in range(chunk_count):
        _create_storage_chunk_record(
            {
                "upload_id": upload_id,
                "chunk_index": idx,
                "vdxf_key": f"chunk.{idx}",
                "status": "pending",
                "label": f"chunk-{idx}",
            }
        )

    return {
        "upload_id": upload_id,
        "status": "pending",
        "chunk_count": chunk_count,
    }


@app.get("/api/storage/upload/{upload_id}", summary="Get storage upload status")
def get_storage_upload(upload_id: str):
    upload = _get_storage_upload_record(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Storage upload not found")

    return {
        "upload": upload,
        "chunks": _list_storage_chunk_records(upload_id),
    }


@app.post("/api/storage/upload/{upload_id}/start", summary="Start storage upload processing")
def start_storage_upload(upload_id: str, api_key: str = Security(_require_api_key)):
    upload = _get_storage_upload_record(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Storage upload not found")
    if upload["status"] != "pending":
        raise HTTPException(status_code=409, detail="Only pending storage uploads can be started")

    conn = _get_db_connection()
    conn.execute(
        "UPDATE storage_uploads SET status = 'uploading', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (upload_id,),
    )
    conn.commit()
    conn.close()
    return {
        "upload_id": upload_id,
        "status": "uploading",
    }


@app.post("/api/storage/upload/{upload_id}/retry", summary="Retry failed storage upload")
def retry_storage_upload(upload_id: str, api_key: str = Security(_require_api_key)):
    upload = _get_storage_upload_record(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Storage upload not found")
    if upload["status"] not in {"failed", "confirming", "uploading"}:
        raise HTTPException(status_code=409, detail="Storage upload is not retryable in current status")

    conn = _get_db_connection()
    conn.execute(
        """
        UPDATE storage_chunks
        SET status = 'pending', txid = NULL, error_message = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE upload_id = ? AND status = 'failed'
        """,
        (upload_id,),
    )
    conn.execute(
        """
        UPDATE storage_uploads
        SET status = 'pending', error_message = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (upload_id,),
    )
    conn.commit()
    conn.close()

    return {
        "upload_id": upload_id,
        "status": "pending",
    }


@app.get("/api/storage/retrieve/{upload_id}", summary="Retrieve and verify stored upload payload")
def retrieve_storage_upload(upload_id: str):
    upload = _get_storage_upload_record(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Storage upload not found")
    if upload["status"] != "complete":
        raise HTTPException(status_code=409, detail="Storage upload is not complete")

    chunks = _list_storage_chunk_records(upload_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="No chunks found for storage upload")

    rpc = _get_rpc_connection(upload["daemon_name"])
    reassembled = bytearray()
    for chunk in chunks:
        reassembled.extend(_chunk_bytes_from_rpc(rpc, upload["identity_fqn"], chunk))

    full_bytes = bytes(reassembled)
    computed_sha256 = hashlib.sha256(full_bytes).hexdigest()
    sha_ok = computed_sha256 == upload["sha256_hex"]
    if not sha_ok:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "SHA256 verification failed",
                "expected": upload["sha256_hex"],
                "computed": computed_sha256,
            },
        )

    return {
        "upload_id": upload_id,
        "sha256_verified": True,
        "size_bytes": len(full_bytes),
        "content_hex": full_bytes.hex(),
    }

# 2. Simple Hello World
@app.get("/")
def read_root():
    """Simple root endpoint for basic connectivity checks."""
    return {"message": "Hello from the new pipeline!"}


@app.get("/register", response_class=HTMLResponse, summary="Simple registration web form")
def register_form():
        allowed_parents = sorted(_allowed_parent_namespaces())
        parent_select_options = "\n".join(
                f'<option value="{parent}">{parent}</option>' for parent in allowed_parents
        )
        parent_hint = (
                "Parent is restricted by server configuration."
                if allowed_parents
                else "No parent allowlist configured; any parent is accepted."
        )

        parent_input_html = (
                f'<select id="parent" name="parent" required>{parent_select_options}</select>'
                if allowed_parents
                else '<input id="parent" name="parent" placeholder="bitcoins.vrsc" required />'
        )

        html = f"""
<!doctype html>
<html lang="en">
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>ID Create Registration</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
            :root {{
                --bg: #f6f4ee;
                --panel: #fffdf8;
                --ink: #1f2a23;
                --accent: #0d7a43;
                --accent-2: #de7a00;
                --line: #d8d0be;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                min-height: 100vh;
                font-family: 'Space Grotesk', sans-serif;
                background: radial-gradient(circle at 85% 15%, #ffe6bf 0%, transparent 45%),
                                        radial-gradient(circle at 10% 90%, #c7f2d8 0%, transparent 40%),
                                        var(--bg);
                color: var(--ink);
                display: grid;
                place-items: center;
                padding: 1.25rem;
            }}
            .shell {{
                width: min(760px, 100%);
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 18px;
                box-shadow: 0 18px 45px rgba(31, 42, 35, 0.12);
                overflow: hidden;
            }}
            .mast {{
                padding: 1.1rem 1.25rem;
                background: linear-gradient(100deg, #d7f0dd 0%, #fff4db 100%);
                border-bottom: 1px solid var(--line);
            }}
            h1 {{ margin: 0; font-size: clamp(1.3rem, 2.5vw, 1.8rem); }}
            .meta {{ margin-top: .35rem; font-size: .9rem; opacity: .85; }}
            form {{
                padding: 1.25rem;
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
                gap: .85rem;
            }}
            label {{ font-size: .82rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }}
            input, select {{
                width: 100%;
                margin-top: .35rem;
                border: 1px solid #bdc7b8;
                border-radius: 10px;
                padding: .62rem .72rem;
                font: 500 .95rem 'IBM Plex Mono', monospace;
                background: #fff;
            }}
            .full {{ grid-column: 1 / -1; }}
            button {{
                border: none;
                border-radius: 12px;
                padding: .8rem 1rem;
                font: 700 .95rem 'Space Grotesk', sans-serif;
                background: linear-gradient(120deg, var(--accent), #0ca25a);
                color: #fff;
                cursor: pointer;
            }}
            #result {{
                margin: 0 1.25rem 1.25rem;
                border: 1px solid var(--line);
                border-radius: 10px;
                padding: .8rem;
                background: #fff;
                font: 400 .88rem 'IBM Plex Mono', monospace;
                white-space: pre-wrap;
                min-height: 4rem;
            }}
            a {{ color: #8f4f00; font-weight: 700; }}
        </style>
    </head>
    <body>
        <div class="shell">
            <div class="mast">
                <h1>Async Registration Console</h1>
                <div class="meta">{parent_hint} Check webhook receipts at <a href="/webhooks/registration-callback">/webhooks/registration-callback</a>.</div>
            </div>
            <form id="register-form">
                <label>Name<input id="name" name="name" placeholder="alice" required /></label>
                <label>Parent{parent_input_html}</label>
                <label>Native Coin<input id="native_coin" name="native_coin" value="VRSC" required /></label>
                <label>Primary R-Address<input id="primary_raddress" name="primary_raddress" placeholder="RaliceAddress" required /></label>
                <label class="full">API Key (X-API-Key)<input id="api_key" name="api_key" autocomplete="off" required /></label>
                <label class="full">Webhook URL<input id="webhook_url" name="webhook_url" /></label>
                <label class="full">Webhook Secret (optional)<input id="webhook_secret" name="webhook_secret" autocomplete="off" /></label>
                <button class="full" type="submit">Submit Registration</button>
            </form>
            <pre id="result">Waiting for submission...</pre>
        </div>
        <script>
            const hookInput = document.getElementById('webhook_url');
            hookInput.value = `${{window.location.origin}}/webhooks/registration-callback`;

            document.getElementById('register-form').addEventListener('submit', async (event) => {{
                event.preventDefault();
                const payload = {{
                    name: document.getElementById('name').value.trim(),
                    parent: document.getElementById('parent').value.trim(),
                    native_coin: document.getElementById('native_coin').value.trim(),
                    primary_raddress: document.getElementById('primary_raddress').value.trim(),
                    webhook_url: hookInput.value.trim() || null,
                    webhook_secret: document.getElementById('webhook_secret').value.trim() || null,
                }};

                const res = await fetch('/api/register', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-API-Key': document.getElementById('api_key').value.trim(),
                    }},
                    body: JSON.stringify(payload),
                }});

                const body = await res.json().catch(() => ({{ error: 'Failed to parse response body.' }}));
                document.getElementById('result').textContent = JSON.stringify({{ status: res.status, body }}, null, 2);
            }});
        </script>
    </body>
</html>
"""
        return HTMLResponse(content=html)


@app.post("/webhooks/registration-callback", summary="Simple webhook receiver for registration events")
async def registration_webhook_callback(request: Request):
        payload: dict | list | str
        try:
                payload = await request.json()
        except Exception:
                payload = (await request.body()).decode("utf-8", errors="replace")

        _store_webhook_event(
                event_name=request.headers.get("X-Webhook-Event"),
                signature=request.headers.get("X-Webhook-Signature"),
                payload=payload,
        )
        return {"ok": True}


@app.get("/webhooks/registration-callback", response_class=HTMLResponse, summary="Webhook receipts viewer")
def registration_webhook_viewer(limit: int = Query(default=20, ge=1, le=200)):
        events = _list_webhook_events(limit=limit)
        cards = []
        for event in events:
                cards.append(
                        (
                                '<article><h3>'
                                + (event.get("event_name") or "(no event header)")
                                + '</h3><p><strong>received_at:</strong> '
                                + str(event.get("received_at"))
                                + '<br /><strong>signature:</strong> '
                                + str(event.get("signature") or "(none)")
                                + '</p><pre>'
                                + event.get("payload_json", "")
                                + "</pre></article>"
                        )
                )

        html = """
<!doctype html>
<html lang="en">
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Webhook Receipts</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400&display=swap');
            body { margin: 0; padding: 1rem; font-family: 'Space Grotesk', sans-serif; background: #fff8eb; color: #2b2215; }
            h1 { margin: 0 0 .4rem; }
            .meta { margin-bottom: 1rem; }
            article { border: 1px solid #d6c4a7; border-radius: 12px; background: #fff; padding: .8rem; margin-bottom: .75rem; }
            h3 { margin: 0 0 .4rem; color: #835200; }
            p { margin: 0 0 .55rem; font-size: .9rem; }
            pre { margin: 0; overflow: auto; background: #f8f3ea; border-radius: 8px; border: 1px solid #e8dfd1; padding: .65rem; font: .84rem 'IBM Plex Mono', monospace; }
        </style>
    </head>
    <body>
        <h1>Webhook Receipts</h1>
        <div class="meta">Showing latest deliveries posted to <code>/webhooks/registration-callback</code>.</div>
        __CARDS__
    </body>
</html>
"""
        return HTMLResponse(content=html.replace("__CARDS__", "".join(cards) if cards else "<p>No webhook events received yet.</p>"))

# 3. An echo endpoint to test data
@app.get("/items/{item_id}")
def read_item(item_id: int, q: str | None = None):
    """Echo test endpoint returning path and optional query parameters."""
    return {"item_id": item_id, "q": q}