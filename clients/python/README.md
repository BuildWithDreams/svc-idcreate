# Python Client (Phase 0)

Thin client for the identity creation service.

## Features

- health check
- create identity request
- status lookup
- list recent failures
- requeue webhook delivery
- create storage upload
- get storage upload status
- start storage upload processing
- retry storage upload
- retrieve storage upload content

## Quick example

```python
from idcreate_client import IdCreateClient, IdCreateApiError

client = IdCreateClient(
    base_url="http://localhost:5003",
    api_key="key1",
)

try:
    created = client.create_identity(
        name="alice",
        parent="bitcoins.vrsc",
        native_coin="VRSC",
        primary_raddress="RaliceAddress",
    )
    request_id = created["request_id"]
    status = client.get_identity_request_status(request_id)
    print(status)
except IdCreateApiError as exc:
    print(exc.status_code, exc.message, exc.body)
```

## Example script

`examples/create_and_poll.py` creates a registration request and polls until terminal status.

Run from repo root:

```bash
IDCREATE_BASE_URL="http://localhost:5003" \
IDCREATE_API_KEY="key1" \
PYTHONPATH="clients/python" \
python clients/python/examples/create_and_poll.py
```

`examples/storage_create_start_poll_retrieve.py` runs storage upload end-to-end:

1. Create storage upload
2. Start processing
3. Poll upload status
4. Retrieve completed content and write bytes to disk

Run from repo root:

```bash
IDCREATE_BASE_URL="http://localhost:5003" \
IDCREATE_API_KEY="key1" \
IDCREATE_STORAGE_NAME="trial1" \
IDCREATE_STORAGE_PARENT="filestorage" \
IDCREATE_STORAGE_NATIVE_COIN="VRSC" \
IDCREATE_STORAGE_PRIMARY_RADDRESS="RaliceAddress" \
IDCREATE_STORAGE_FILE_PATH="/tmp/book.json" \
IDCREATE_STORAGE_OUTPUT_FILE="/tmp/book.retrieved.bin" \
PYTHONPATH="clients/python" \
python clients/python/examples/storage_create_start_poll_retrieve.py
```

Notes:

- Worker must be running for status to advance to `complete`.
- If upload reaches `failed`, inspect `/api/storage/upload/{upload_id}` and retry after triage.

## Storage example

```python
from idcreate_client import IdCreateClient

client = IdCreateClient(
    base_url="http://localhost:5003",
    api_key="key1",
)

created = client.create_storage_upload(
    name="trial1",
    parent="filestorage",
    native_coin="VRSC",
    primary_raddress="RaliceAddress",
    file_path="/tmp/book.json",
    mime_type="application/json",
)

upload_id = created["upload_id"]
client.start_storage_upload(upload_id)
status = client.get_storage_upload_status(upload_id)
print(status)

# After worker marks upload complete
retrieved = client.retrieve_storage_upload(upload_id)
print(retrieved["sha256_verified"], retrieved["size_bytes"])
```
