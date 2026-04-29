# VerusID contentmultimap On-Chain Storage Implementation Guide

This guide describes how to add server-side support for Verus on-chain file storage via identity contentmultimap updates, aligned to this repository's existing architecture.

Scope:
- Add upload and retrieval workflows for data stored under a VerusID contentmultimap.
- Use the recommended `updateidentity + data` wrapper approach for files larger than a few KB.
- Keep this service as the orchestration layer over RPC (not a full wallet replacement).

## 1. Goals and Non-Goals

Goals:
- Support storing file chunks on-chain using identity `contentmultimap` entries.
- Track all upload txids so retrieval is reliable.
- Expose API endpoints for upload lifecycle and retrieval.
- Reuse existing async/worker pattern from identity registration.

Non-goals (MVP):
- Parallel chunk uploads to the same identity (protocol prevents this).
- Full wallet UX for key management.
- Cross-chain data references in the first version.

## 2. Architecture Fit (Current Repo)

Current components:
- `id_create_service.py`: HTTP API and SQLite persistence.
- `worker.py`: retry/state advancement for async jobs.
- `rpc_manager.py`: daemon connection cache.
- `verus_node_rpc.py`: RPC wrapper around `slickrpc.Proxy`.

Recommended extension:
- Add storage-specific API routes and DB tables in `id_create_service.py`.
- Add storage job processor in `worker.py`.
- Add required RPC methods in `verus_node_rpc.py`.
- Reuse `rpc_manager.py` for daemon selection by native coin.

## 3. Storage Strategy Choices

Use three modes, but default to one:

1. `identity_data_wrapper` (default)
- RPC: `updateidentity` with `contentmultimap` value containing `{ "data": { ... } }`.
- Pros: lower cost than `sendcurrency`, automatic chunking via BreakApart, linked to identity.
- Required for medium/large payloads.

2. `raw_contentmultimap`
- RPC: `updateidentity` with hex string value.
- Use only for very small metadata payloads (target < 4KB).

3. `sendcurrency`
- RPC: `sendcurrency` to z-address with data object.
- Keep for future optional privacy mode due to higher cost and async op handling.

## 4. Data Model Additions (SQLite)

Add two new tables.

`storage_uploads`:
- `id` TEXT PK (uuid)
- `requested_name` TEXT NOT NULL (sub-id name, for example `trial1`)
- `parent_namespace` TEXT NOT NULL (for example `filestorage`)
- `identity_fqn` TEXT NOT NULL (for example `trial1.filestorage@`)
- `native_coin` TEXT NOT NULL
- `daemon_name` TEXT NOT NULL
- `status` TEXT NOT NULL
  - `pending`
  - `uploading`
  - `confirming`
  - `complete`
  - `failed`
- `file_path` TEXT NOT NULL
- `mime_type` TEXT
- `file_size` INTEGER NOT NULL
- `sha256_hex` TEXT NOT NULL
- `chunk_size_bytes` INTEGER NOT NULL DEFAULT 999000
- `chunk_count` INTEGER NOT NULL
- `current_chunk_index` INTEGER NOT NULL DEFAULT 0
- `error_message` TEXT
- `created_at`, `updated_at`

`storage_chunks`:
- `id` INTEGER PK AUTOINCREMENT
- `upload_id` TEXT NOT NULL
- `chunk_index` INTEGER NOT NULL
- `vdxf_key` TEXT NOT NULL
- `txid` TEXT
- `status` TEXT NOT NULL (`pending`, `submitted`, `confirmed`, `failed`)
- `label` TEXT
- `ivk` TEXT
- `epk` TEXT
- `objectdata_ref_json` TEXT
- `error_message` TEXT
- unique(`upload_id`, `chunk_index`)

Why separate chunk rows:
- strict txid tracking per chunk
- resumable processing
- easier retry and audit

## 5. RPC Wrapper Additions

Implement these methods in `verus_node_rpc.py`:

- `get_vdxfid(name: str) -> dict`
  - wraps `getvdxfid`
- `update_identity(update_payload: dict) -> dict`
  - wraps `updateidentity`
- `get_identity_content(identity: str, height_start=0, height_end=0, txproofs=False, txproofheight=0, vdxfkey=None, keepdeleted=False) -> dict`
  - wraps `getidentitycontent`
- `decrypt_data(payload: dict) -> list[dict]`
  - wraps `decryptdata`
- optional: `sign_data(payload: dict) -> dict`
  - wraps `signdata` for advanced workflows

Implementation note:
- Keep method signatures pythonic, but pass raw JSON structure directly to RPC.
- Normalize RPC exceptions into consistent messages for API layer mapping.

## 6. API Design (Service)

Add endpoints in `id_create_service.py`.

1. `POST /api/storage/upload` (authenticated)
- Input:
  - `name`, `parent`, `native_coin`, `primary_raddress`
  - `file_path`
  - optional `mime_type`
  - optional `chunk_size_bytes` (default 999000)
- Behavior:
  - validate file exists and size
  - compute sha256
  - split locally into deterministic chunks
  - create `storage_uploads` + `storage_chunks` records
  - return `upload_id` with `pending`

2. `GET /api/storage/upload/{upload_id}`
- returns upload summary and chunk progress

3. `POST /api/storage/upload/{upload_id}/start` (authenticated)
- flips status to `uploading` so worker picks it up

4. `GET /api/storage/retrieve/{upload_id}`
- server reads chunk txids + VDXF keys from DB
- calls `getidentitycontent` and `decryptdata`
- reassembles bytes, verifies SHA256, returns metadata or downloads file

5. `POST /api/storage/upload/{upload_id}/retry` (authenticated)
- reset failed chunks or from an index forward

## 7. Worker Flow (Sequential Identity Updates)

Processing loop per upload:

1. load next `pending` chunk ordered by `chunk_index`
2. build `updateidentity` payload with `contentmultimap[vdxf_key] = [{"data": {...}}]`
3. submit RPC
4. store returned txid on `storage_chunks`
5. wait for confirmation before next chunk (protocol sequencing)
6. continue until all chunks confirmed
7. write final metadata keys (filename, hash, filesize, chunkcount)
8. mark upload `complete`

Important rule:
- Never process two chunks concurrently for the same identity.

## 8. Payload Construction Rules

Correct placement (must be nested):

```json
{
  "parent": "iParentAddress",
  "name": "trial1",
  "primaryaddresses": ["R..."] ,
  "minimumsignatures": 1,
  "contentmultimap": {
    "iVdxfChunkKey": [
      {
        "data": {
          "address": "trial1.filestorage@",
          "filename": "/tmp/chunks/chunk_0000.bin",
          "createmmr": true,
          "label": "chunk-0",
          "mimetype": "application/octet-stream"
        }
      }
    ]
  }
}
```

Incorrect placement (ignored for chunking):

```json
{
  "name": "trial1",
  "data": {
    "filename": "/tmp/chunks/chunk_0000.bin"
  }
}
```

## 9. VDXF Key Strategy

Use deterministic key naming under namespace:
- `filestorage::chunk.0`
- `filestorage::chunk.1`
- ...
- `filestorage::filename`
- `filestorage::mimetype`
- `filestorage::filesize`
- `filestorage::hash`
- `filestorage::chunkcount`

At startup or upload initialization:
- resolve names via `getvdxfid`
- cache mapping in memory or DB table

## 10. Retrieval Strategy

For each chunk:
- call `getidentitycontent(identity_fqn, ..., vdxfkey=chunk_key)`
- use first datadescriptor entry for encrypted data reference
- call `decryptdata` with:
  - datadescriptor fields
  - real upload txid for that chunk
  - same ivk from descriptor
  - `retrieve: true`
- decode returned `objectdata` hex to bytes

After all chunks:
- sort by chunk index
- concatenate bytes
- verify SHA256 equals `storage_uploads.sha256_hex`
- return file bytes or persist reconstructed file

## 11. Error Handling and Recovery

Map to API statuses:
- invalid request/file path: 400
- auth failure: 403
- upload id not found: 404
- bad state transition: 409
- daemon unavailable/rpc failure: 503

Retry policy:
- transient RPC errors: exponential backoff
- mempool/confirmation delays: poll with timeout and retry
- permanent precheck errors: mark chunk failed and stop upload

Auditability:
- log `upload_id`, `chunk_index`, `vdxf_key`, `txid`
- keep original RPC error text in DB `error_message`

## 12. Security and Ops Considerations

- Keep RPC credentials only in environment variables.
- Restrict `file_path` to an allowed base directory to avoid arbitrary filesystem access.
- Validate max file size in API (`MAX_UPLOAD_BYTES` env).
- Do not delete local chunk files until completion and integrity verification.
- Because processing is CPU-heavy, tune worker concurrency globally but force per-identity serialization.

Suggested env vars:
- `STORAGE_ENABLED=true`
- `STORAGE_ALLOWED_BASE_DIR=/var/lib/verus-storage-input`
- `STORAGE_MAX_UPLOAD_BYTES=20000000`
- `STORAGE_CHUNK_BYTES=999000`
- `STORAGE_CONFIRM_TIMEOUT_SECONDS=900`
- `STORAGE_CONFIRM_POLL_SECONDS=10`

## 13. Implementation Phases

Phase 1: RPC and schema
- Add RPC methods in `verus_node_rpc.py`
- Add DB migrations/table init for storage tables
- Add upload status endpoint

Phase 2: Upload pipeline
- Add upload create/start endpoints
- Add worker chunk submission and confirmation tracking
- Add metadata finalization writes

Phase 3: Retrieval pipeline
- Add retrieval endpoint using `getidentitycontent + decryptdata`
- Add reassembly and hash verification

Phase 4: Hardening
- Add retries, metrics, and structured logs
- Add admin retry endpoint and operational dashboards

## 14. Test Plan

Unit tests:
- payload builder puts `data` under `contentmultimap`
- chunk splitter produces deterministic chunk counts and labels
- RPC error mapping

Service tests:
- upload create validates file constraints
- upload state transitions and conflict cases
- retry endpoint behavior

Worker tests:
- sequential chunk processing
- stop on failed chunk
- resume from partial state

Integration tests (vrsctest):
- upload small file (< 6KB)
- upload medium file (for example 100KB)
- retrieve and verify byte-for-byte SHA256 equality

## 15. Minimal MVP Checklist

- [ ] `verus_node_rpc.py` methods for `updateidentity`, `getidentitycontent`, `decryptdata`, `getvdxfid`
- [ ] DB tables: `storage_uploads`, `storage_chunks`
- [ ] `POST /api/storage/upload`
- [ ] `GET /api/storage/upload/{upload_id}`
- [ ] worker support for sequential chunk writes
- [ ] txid tracking per chunk
- [ ] retrieval endpoint with SHA256 verification
- [ ] test coverage for happy path and failure path

## 16. Practical First Increment

Start with one constrained flow:
- file size <= 999000 bytes
- single `updateidentity + data` call
- store one txid
- retrieval and hash verification

Then expand to multi-chunk and resume/retry support. This gives a fast, testable vertical slice before tackling full multi-transaction orchestration.
