import pathlib
import sqlite3
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import id_create_service


def _build_client(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setenv("REGISTRAR_API_KEYS", "test-key")
    monkeypatch.setenv("STORAGE_ALLOWED_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_MAX_UPLOAD_BYTES", "20000000")

    with TestClient(id_create_service.app) as client:
        yield client


def _seed_upload(upload_id: str, status: str):
    id_create_service._create_storage_upload_record(
        {
            "id": upload_id,
            "requested_name": "trial1",
            "parent_namespace": "filestorage",
            "identity_fqn": "trial1.filestorage@",
            "native_coin": "VRSC",
            "daemon_name": "verusd_vrsc",
            "status": status,
            "file_path": "/tmp/file.bin",
            "mime_type": "application/octet-stream",
            "file_size": 10,
            "sha256_hex": "abc123",
            "chunk_size_bytes": 999000,
            "chunk_count": 2,
        }
    )


def test_storage_upload_create_happy_path_red(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    file_path = input_dir / "book.json"
    file_path.write_text('{"hello":"world"}')

    payload = {
        "name": "trial1",
        "parent": "filestorage",
        "native_coin": "VRSC",
        "primary_raddress": "RaliceAddress",
        "file_path": str(file_path),
        "mime_type": "application/json",
        "chunk_size_bytes": 999000,
    }

    resp = client.post(
        "/api/storage/upload",
        json=payload,
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 202
    data = resp.json()
    assert data["upload_id"]
    assert data["status"] == "pending"



def test_storage_upload_status_returns_upload_and_chunks_red(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    upload_id = str(uuid.uuid4())
    _seed_upload(upload_id, "pending")
    id_create_service._create_storage_chunk_record(
        {
            "upload_id": upload_id,
            "chunk_index": 0,
            "vdxf_key": "iChunk0",
            "status": "pending",
            "label": "chunk-0",
        }
    )

    resp = client.get(f"/api/storage/upload/{upload_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["upload"]["id"] == upload_id
    assert len(data["chunks"]) == 1



def test_storage_upload_start_transitions_to_uploading_red(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    upload_id = str(uuid.uuid4())
    _seed_upload(upload_id, "pending")

    resp = client.post(
        f"/api/storage/upload/{upload_id}/start",
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "uploading"

    row = id_create_service._get_storage_upload_record(upload_id)
    assert row is not None
    assert row["status"] == "uploading"



def test_storage_upload_retry_resets_failed_chunks_red(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    upload_id = str(uuid.uuid4())
    _seed_upload(upload_id, "failed")
    id_create_service._create_storage_chunk_record(
        {
            "upload_id": upload_id,
            "chunk_index": 0,
            "vdxf_key": "iChunk0",
            "status": "failed",
            "label": "chunk-0",
            "error_message": "bad-txns-failed-precheck",
        }
    )

    resp = client.post(
        f"/api/storage/upload/{upload_id}/retry",
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    conn.row_factory = sqlite3.Row
    chunk = conn.execute(
        "SELECT status, txid, error_message FROM storage_chunks WHERE upload_id = ? AND chunk_index = 0",
        (upload_id,),
    ).fetchone()
    conn.close()

    assert chunk["status"] == "pending"
    assert chunk["txid"] is None
    assert chunk["error_message"] is None
