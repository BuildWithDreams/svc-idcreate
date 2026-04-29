import pathlib
import sys
import sqlite3
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import id_create_service


def test_select_storage_mode_prefers_raw_for_tiny_payloads():
    # Phase 0 red test: service should expose deterministic mode selection.
    mode = id_create_service._select_storage_mode(payload_size_bytes=1024)
    assert mode == "raw_contentmultimap"


def test_select_storage_mode_prefers_wrapper_above_safe_raw_limit():
    # Above safe raw threshold, service should force data-wrapper mode.
    mode = id_create_service._select_storage_mode(payload_size_bytes=6000)
    assert mode == "identity_data_wrapper"


def test_build_storage_entry_includes_nested_data_wrapper_shape():
    entry = id_create_service._build_storage_contentmultimap_entry(
        vdxf_key="iChunkKey",
        identity_address="trial1.filestorage@",
        filename="/tmp/chunk-0.json",
        label="page-0",
        mimetype="application/json",
    )

    assert entry == {
        "iChunkKey": [
            {
                "data": {
                    "address": "trial1.filestorage@",
                    "filename": "/tmp/chunk-0.json",
                    "createmmr": True,
                    "label": "page-0",
                    "mimetype": "application/json",
                }
            }
        ]
    }


def test_storage_schema_tables_exist(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))

    id_create_service._init_db()

    conn = sqlite3.connect(str(db_path))
    table_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('storage_uploads', 'storage_chunks')"
        ).fetchall()
    }
    conn.close()

    assert "storage_uploads" in table_names
    assert "storage_chunks" in table_names


def test_storage_upload_and_chunk_records_persist(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    id_create_service._init_db()

    upload_id = str(uuid.uuid4())
    id_create_service._create_storage_upload_record(
        {
            "id": upload_id,
            "requested_name": "trial1",
            "parent_namespace": "filestorage",
            "identity_fqn": "trial1.filestorage@",
            "native_coin": "VRSC",
            "daemon_name": "verusd_vrsc",
            "status": "pending",
            "file_path": "/tmp/file.bin",
            "mime_type": "application/octet-stream",
            "file_size": 10,
            "sha256_hex": "abc123",
            "chunk_size_bytes": 999000,
            "chunk_count": 2,
        }
    )
    id_create_service._create_storage_chunk_record(
        {
            "upload_id": upload_id,
            "chunk_index": 0,
            "vdxf_key": "iChunk0",
            "status": "pending",
            "label": "chunk-0",
        }
    )

    # Re-open through helpers to ensure durable persistence behavior.
    upload = id_create_service._get_storage_upload_record(upload_id)
    chunks = id_create_service._list_storage_chunk_records(upload_id)

    assert upload is not None
    assert upload["id"] == upload_id
    assert upload["status"] == "pending"
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0


def test_storage_chunk_unique_constraint_enforced(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    id_create_service._init_db()

    upload_id = str(uuid.uuid4())
    id_create_service._create_storage_upload_record(
        {
            "id": upload_id,
            "requested_name": "trial1",
            "parent_namespace": "filestorage",
            "identity_fqn": "trial1.filestorage@",
            "native_coin": "VRSC",
            "daemon_name": "verusd_vrsc",
            "status": "pending",
            "file_path": "/tmp/file.bin",
            "mime_type": "application/octet-stream",
            "file_size": 10,
            "sha256_hex": "abc123",
            "chunk_size_bytes": 999000,
            "chunk_count": 1,
        }
    )

    id_create_service._create_storage_chunk_record(
        {
            "upload_id": upload_id,
            "chunk_index": 0,
            "vdxf_key": "iChunk0",
            "status": "pending",
            "label": "chunk-0",
        }
    )

    try:
        id_create_service._create_storage_chunk_record(
            {
                "upload_id": upload_id,
                "chunk_index": 0,
                "vdxf_key": "iChunk0",
                "status": "pending",
                "label": "chunk-0-dup",
            }
        )
        assert False, "Expected sqlite3.IntegrityError for duplicate upload_id/chunk_index"
    except sqlite3.IntegrityError:
        assert True


def test_namespace_key_map_is_deterministic_for_same_inputs():
    first = id_create_service._build_namespace_key_map(namespace="clever", page_count=3)
    second = id_create_service._build_namespace_key_map(namespace="clever", page_count=3)

    assert first == second
    assert first["manifest_name"] == "clever::manifest"
    assert first["tx_map_name"] == "clever::tx_map"
    assert first["page_names"] == ["clever::page.0", "clever::page.1", "clever::page.2"]


def test_namespace_key_map_requires_positive_page_count():
    try:
        id_create_service._build_namespace_key_map(namespace="clever", page_count=0)
        assert False, "Expected ValueError when page_count is less than 1"
    except ValueError:
        assert True


def test_namespace_slug_normalization_is_stable():
    normalized = id_create_service._normalize_namespace_slug("  Clever::Book Vault  ")
    assert normalized == "clever_book_vault"
