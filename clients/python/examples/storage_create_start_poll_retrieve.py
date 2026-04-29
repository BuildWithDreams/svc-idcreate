import os
import pathlib
import time

from idcreate_client import IdCreateApiError, IdCreateClient


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main():
    base_url = os.getenv("IDCREATE_BASE_URL", "http://localhost:5003")
    api_key = os.getenv("IDCREATE_API_KEY", "")

    name = os.getenv("IDCREATE_STORAGE_NAME", "trial1")
    parent = os.getenv("IDCREATE_STORAGE_PARENT", "filestorage")
    native_coin = os.getenv("IDCREATE_STORAGE_NATIVE_COIN", "VRSC")
    primary_raddress = os.getenv("IDCREATE_STORAGE_PRIMARY_RADDRESS", "RaliceAddress")

    file_path = os.getenv("IDCREATE_STORAGE_FILE_PATH", "").strip()
    mime_type = os.getenv("IDCREATE_STORAGE_MIME_TYPE", "application/octet-stream")
    chunk_size_bytes = int(os.getenv("IDCREATE_STORAGE_CHUNK_SIZE_BYTES", "999000"))

    wait_timeout_seconds = int(os.getenv("IDCREATE_STORAGE_WAIT_TIMEOUT_SECONDS", "600"))
    poll_seconds = int(os.getenv("IDCREATE_STORAGE_POLL_SECONDS", "5"))
    auto_start = _truthy(os.getenv("IDCREATE_STORAGE_AUTO_START", "true"))

    output_file = os.getenv("IDCREATE_STORAGE_OUTPUT_FILE", "").strip()

    if not file_path:
        raise ValueError("IDCREATE_STORAGE_FILE_PATH must be set to a readable file path")

    file_obj = pathlib.Path(file_path)
    if not file_obj.is_file():
        raise ValueError(f"Input file does not exist: {file_path}")

    client = IdCreateClient(base_url=base_url, api_key=api_key)

    try:
        created = client.create_storage_upload(
            name=name,
            parent=parent,
            native_coin=native_coin,
            primary_raddress=primary_raddress,
            file_path=str(file_obj),
            mime_type=mime_type,
            chunk_size_bytes=chunk_size_bytes,
        )
        upload_id = created["upload_id"]
        print(f"Created upload_id={upload_id} status={created.get('status')} chunk_count={created.get('chunk_count')}")

        if auto_start:
            started = client.start_storage_upload(upload_id)
            print(f"Started upload_id={upload_id} status={started.get('status')}")

        deadline = time.time() + wait_timeout_seconds
        final_status = None

        while time.time() < deadline:
            status_payload = client.get_storage_upload_status(upload_id)
            upload = status_payload.get("upload", {})
            final_status = upload.get("status")

            chunk_rows = status_payload.get("chunks", [])
            total_chunks = len(chunk_rows)
            confirmed_chunks = sum(1 for c in chunk_rows if c.get("status") == "confirmed")
            submitted_chunks = sum(1 for c in chunk_rows if c.get("status") == "submitted")
            failed_chunks = sum(1 for c in chunk_rows if c.get("status") == "failed")

            print(
                "status="
                f"{final_status} "
                f"confirmed={confirmed_chunks}/{total_chunks} "
                f"submitted={submitted_chunks} failed={failed_chunks}"
            )

            if final_status in {"complete", "failed"}:
                break

            time.sleep(poll_seconds)

        if final_status != "complete":
            if final_status == "failed":
                print("Upload reached failed state. Use retry endpoint after triage if needed.")
                return
            print("Timed out waiting for complete state.")
            return

        retrieved = client.retrieve_storage_upload(upload_id)
        print(
            f"Retrieved upload_id={upload_id} "
            f"sha256_verified={retrieved.get('sha256_verified')} "
            f"size_bytes={retrieved.get('size_bytes')}"
        )

        content_hex = retrieved.get("content_hex", "")
        if content_hex:
            output_path = pathlib.Path(output_file) if output_file else pathlib.Path(f"/tmp/retrieved_{upload_id}.bin")
            output_path.write_bytes(bytes.fromhex(content_hex))
            print(f"Wrote retrieved bytes to {output_path}")

    except IdCreateApiError as exc:
        print(f"API error: status={exc.status_code} message={exc.message} body={exc.body}")
        raise


if __name__ == "__main__":
    main()
