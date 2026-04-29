import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import id_create_service
import worker


def _prepare_db(monkeypatch, tmp_path):
    db_path = tmp_path / "registrar.db"
    monkeypatch.setenv("REGISTRAR_DB_PATH", str(db_path))
    id_create_service._init_db()

    conn = sqlite3.connect(str(db_path))
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
            current_chunk_index
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "upload-1",
            "trial1",
            "filestorage",
            "trial1.filestorage@",
            "VRSC",
            "verusd_vrsc",
            "uploading",
            "/tmp/file.bin",
            "application/octet-stream",
            2,
            "abc123",
            999000,
            2,
            0,
        ),
    )
    conn.execute(
        "INSERT INTO storage_chunks (upload_id, chunk_index, vdxf_key, txid, status) VALUES (?, ?, ?, ?, ?)",
        ("upload-1", 0, "iChunk0", None, "pending"),
    )
    conn.execute(
        "INSERT INTO storage_chunks (upload_id, chunk_index, vdxf_key, txid, status) VALUES (?, ?, ?, ?, ?)",
        ("upload-1", 1, "iChunk1", None, "pending"),
    )
    conn.commit()
    conn.close()


class _FakeStorageRpc:
    def __init__(self):
        self.calls = []

    def update_identity(self, update_payload):
        self.calls.append(update_payload)
        return {"txid": "txid-storage-1"}


class _FakeStorageRpcWithConfirm:
    def __init__(self, confirmations=1):
        self.calls = []
        self.confirmations = confirmations

    def update_identity(self, update_payload):
        self.calls.append(update_payload)
        return {"txid": "txid-storage-1"}

    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": self.confirmations}


class _FakeStorageRpcSubmitFails:
    def update_identity(self, update_payload):
        raise Exception("bad-txns-failed-precheck")

    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 0}


class _FakeStorageRpcSubmitTimeout:
    def update_identity(self, update_payload):
        raise Exception("connection timeout")

    def get_raw_transaction(self, txid, verbose=1):
        return {"txid": txid, "confirmations": 0}


def test_process_next_storage_chunk_submits_only_one_chunk_per_call(monkeypatch, tmp_path):
    # Phase 0 red test: worker should expose one-step sequenced processor.
    _prepare_db(monkeypatch, tmp_path)

    fake_rpc = _FakeStorageRpc()
    result = worker.process_next_storage_chunk(upload_id="upload-1", rpc_connection=fake_rpc)

    assert result["submitted"] == 1
    assert len(fake_rpc.calls) == 1


def test_process_storage_upload_once_submits_then_moves_to_confirming_red(monkeypatch, tmp_path):
    _prepare_db(monkeypatch, tmp_path)

    rpc = _FakeStorageRpcWithConfirm(confirmations=0)
    result = worker.process_storage_upload_once(upload_id="upload-1", rpc_connection=rpc)

    assert result["state"] == "confirming"
    assert result["submitted"] == 1


def test_process_storage_upload_once_marks_complete_when_all_chunks_confirmed_red(monkeypatch, tmp_path):
    _prepare_db(monkeypatch, tmp_path)

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    conn.execute(
        "UPDATE storage_chunks SET status = 'submitted', txid = 'txid-storage-0' WHERE upload_id = ? AND chunk_index = 0",
        ("upload-1",),
    )
    conn.execute(
        "UPDATE storage_chunks SET status = 'submitted', txid = 'txid-storage-1' WHERE upload_id = ? AND chunk_index = 1",
        ("upload-1",),
    )
    conn.execute("UPDATE storage_uploads SET status = 'confirming' WHERE id = ?", ("upload-1",))
    conn.commit()
    conn.close()

    rpc = _FakeStorageRpcWithConfirm(confirmations=1)
    result = worker.process_storage_upload_once(upload_id="upload-1", rpc_connection=rpc)

    assert result["state"] == "complete"


def test_process_storage_upload_once_marks_failed_on_submit_error_red(monkeypatch, tmp_path):
    _prepare_db(monkeypatch, tmp_path)

    rpc = _FakeStorageRpcSubmitFails()
    result = worker.process_storage_upload_once(upload_id="upload-1", rpc_connection=rpc)

    assert result["state"] == "failed"


def test_process_storage_upload_once_resumes_without_resubmitting_confirmed_red(monkeypatch, tmp_path):
    _prepare_db(monkeypatch, tmp_path)

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    conn.execute(
        "UPDATE storage_chunks SET status = 'confirmed', txid = 'txid-storage-0' WHERE upload_id = ? AND chunk_index = 0",
        ("upload-1",),
    )
    conn.execute("UPDATE storage_uploads SET status = 'uploading', current_chunk_index = 0 WHERE id = ?", ("upload-1",))
    conn.commit()
    conn.close()

    rpc = _FakeStorageRpcWithConfirm(confirmations=0)
    result = worker.process_storage_upload_once(upload_id="upload-1", rpc_connection=rpc)

    assert result["state"] in {"uploading", "confirming"}
    # Only one new submit should occur (chunk 1), not chunk 0 again.
    assert len(rpc.calls) == 1


def test_process_storage_upload_once_schedules_retry_for_transient_submit_error_red(monkeypatch, tmp_path):
    _prepare_db(monkeypatch, tmp_path)
    monkeypatch.setenv("WORKER_MAX_RETRIES", "3")
    monkeypatch.setenv("WORKER_RETRY_BASE_SECONDS", "15")

    rpc = _FakeStorageRpcSubmitTimeout()
    result = worker.process_storage_upload_once(upload_id="upload-1", rpc_connection=rpc)

    assert result["state"] == "retry_scheduled"

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, attempts, next_retry_at, error_message FROM storage_uploads WHERE id = ?",
        ("upload-1",),
    ).fetchone()
    conn.close()

    assert row["status"] == "uploading"
    assert row["attempts"] == 1
    assert row["next_retry_at"] is not None
    assert "timeout" in row["error_message"]


def test_process_storage_once_entrypoint_processes_only_due_uploads_red(monkeypatch, tmp_path):
    _prepare_db(monkeypatch, tmp_path)

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    conn.execute(
        """
        INSERT INTO storage_uploads (
            id, requested_name, parent_namespace, identity_fqn, native_coin, daemon_name,
            status, file_path, mime_type, file_size, sha256_hex, chunk_size_bytes, chunk_count,
            current_chunk_index, attempts, next_retry_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+5 minutes'))
        """,
        (
            "upload-2",
            "trial2",
            "filestorage",
            "trial2.filestorage@",
            "VRSC",
            "verusd_vrsc",
            "uploading",
            "/tmp/file2.bin",
            "application/octet-stream",
            2,
            "def456",
            999000,
            1,
            0,
            1,
        ),
    )
    conn.execute(
        "INSERT INTO storage_chunks (upload_id, chunk_index, vdxf_key, txid, status) VALUES (?, ?, ?, ?, ?)",
        ("upload-2", 0, "iChunkX", None, "pending"),
    )
    conn.commit()
    conn.close()

    rpc = _FakeStorageRpcWithConfirm(confirmations=0)
    monkeypatch.setattr(worker, "_get_rpc_connection", lambda daemon_name: rpc)

    processed = worker.process_storage_once()
    assert processed == 1

    conn = sqlite3.connect(str(tmp_path / "registrar.db"))
    conn.row_factory = sqlite3.Row
    first = conn.execute("SELECT status FROM storage_uploads WHERE id = ?", ("upload-1",)).fetchone()
    second = conn.execute("SELECT status FROM storage_uploads WHERE id = ?", ("upload-2",)).fetchone()
    conn.close()

    assert first["status"] == "confirming"
    assert second["status"] == "uploading"
