import hashlib
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import id_create_service


class _FakeStorageRpc:
    def get_identity_content(self, identity_name_or_id, height_start=0, height_end=0, tx_proofs=False, tx_proof_height=0, vdxf_key=None, keep_deleted=False):
        return {
            "identity": identity_name_or_id,
            "vdxf_key": vdxf_key,
            "items": [
                {
                    "datadescriptor": {
                        "version": 1,
                        "flags": 13,
                        "objectdata": "deadbeef",
                        "epk": "epk1",
                        "ivk": "ivk1",
                    }
                }
            ],
        }

    def decrypt_data(self, payload):
        txid = payload.get("txid")
        if txid == "txid-chunk-0":
            return [{"objectdata": "68656c6c6f20", "label": "chunk-0"}]  # hello 
        if txid == "txid-chunk-1":
            return [{"objectdata": "776f726c64", "label": "chunk-1"}]  # world
        return [{"objectdata": "", "label": "unknown"}]


def _build_client(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    monkeypatch.setattr(id_create_service, "_get_rpc_connection", lambda _: _FakeStorageRpc())

    with TestClient(id_create_service.app) as client:
        yield client


def _seed_complete_upload(upload_id: str):
    full_bytes = b"hello world"
    id_create_service._create_storage_upload_record(
        {
            "id": upload_id,
            "requested_name": "trial1",
            "parent_namespace": "filestorage",
            "identity_fqn": "trial1.filestorage@",
            "native_coin": "VRSC",
            "daemon_name": "verusd_vrsc",
            "status": "complete",
            "file_path": "/tmp/file.bin",
            "mime_type": "application/octet-stream",
            "file_size": len(full_bytes),
            "sha256_hex": hashlib.sha256(full_bytes).hexdigest(),
            "chunk_size_bytes": 6,
            "chunk_count": 2,
        }
    )
    id_create_service._create_storage_chunk_record(
        {
            "upload_id": upload_id,
            "chunk_index": 0,
            "vdxf_key": "chunk.0",
            "txid": "txid-chunk-0",
            "status": "confirmed",
            "label": "chunk-0",
            "ivk": "ivk1",
            "epk": "epk1",
            "objectdata_ref_json": "{}",
        }
    )
    id_create_service._create_storage_chunk_record(
        {
            "upload_id": upload_id,
            "chunk_index": 1,
            "vdxf_key": "chunk.1",
            "txid": "txid-chunk-1",
            "status": "confirmed",
            "label": "chunk-1",
            "ivk": "ivk1",
            "epk": "epk1",
            "objectdata_ref_json": "{}",
        }
    )


def test_storage_retrieve_reassembles_and_verifies_sha256_red(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    upload_id = str(uuid.uuid4())
    _seed_complete_upload(upload_id)

    resp = client.get(f"/api/storage/retrieve/{upload_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["upload_id"] == upload_id
    assert data["sha256_verified"] is True
    assert data["size_bytes"] == 11
    assert data["content_hex"] == "68656c6c6f20776f726c64"


def test_storage_retrieve_rejects_non_complete_upload_red(monkeypatch, tmp_path):
    client = next(_build_client(monkeypatch, tmp_path))

    upload_id = str(uuid.uuid4())
    id_create_service._create_storage_upload_record(
        {
            "id": upload_id,
            "requested_name": "trial1",
            "parent_namespace": "filestorage",
            "identity_fqn": "trial1.filestorage@",
            "native_coin": "VRSC",
            "daemon_name": "verusd_vrsc",
            "status": "uploading",
            "file_path": "/tmp/file.bin",
            "mime_type": "application/octet-stream",
            "file_size": 1,
            "sha256_hex": "00",
            "chunk_size_bytes": 999000,
            "chunk_count": 1,
        }
    )

    resp = client.get(f"/api/storage/retrieve/{upload_id}")

    assert resp.status_code == 409
