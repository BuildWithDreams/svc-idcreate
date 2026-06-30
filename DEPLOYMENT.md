# Deployment Guide

## Overview

This service has two runtime components:

- API service: FastAPI app (`id_create_service.py`)
- Worker service: background state machine (`worker.py`)

Recommended deployment is PostgreSQL via `DATABASE_URL`.
SQLite remains available only as a fallback when `DATABASE_URL` is unset.

## 1. Prepare environment

Create a `.env` file from `env.sample` and set at minimum:

```env
# API auth
REGISTRAR_API_KEYS=key1,key2

# service wallet
SOURCE_OF_FUNDS=RsourceFundsAddr

# PostgreSQL (recommended)
DATABASE_URL=postgresql://idcreate:idcreate@postgres:5432/idcreate

# worker retries
WORKER_MAX_RETRIES=5
WORKER_RETRY_BASE_SECONDS=15

# webhook retries
WEBHOOK_TIMEOUT_SECONDS=5
WEBHOOK_MAX_RETRIES=5
WEBHOOK_RETRY_BASE_SECONDS=15
WEBHOOK_SIGNING_SECRET=

# health fallback daemon
HEALTH_RPC_DAEMON=verusd_vrsc

# daemon RPC enablement and credentials
verusd_vrsc_rpc_enabled=true
verusd_vrsc_rpc_user=...
verusd_vrsc_rpc_password=...
verusd_vrsc_rpc_port=...
verusd_vrsc_rpc_host=...
```

Optional SQLite fallback (not recommended for high concurrency):

```env
REGISTRAR_DB_PATH=/data/registrar.db
```

## 2. Build and start with Docker Compose

Use the sample stack (includes PostgreSQL, API, and worker):

```bash
docker compose -f sample.docker-compose.yaml up -d --build
```

Check status:

```bash
docker compose -f sample.docker-compose.yaml ps
```

Tail logs:

```bash
docker compose -f sample.docker-compose.yaml logs -f idcreate-api
docker compose -f sample.docker-compose.yaml logs -f idcreate-worker
docker compose -f sample.docker-compose.yaml logs -f postgres
```

## 3. Verify deployment

Health:

```bash
curl -s "http://localhost:5003/health?native_coin=VRSC"
```

Swagger:

- `http://localhost:5003/docs`

Quick registration smoke test:

```bash
curl -s -X POST "http://localhost:5003/api/register" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: key1" \
  -d '{
    "name": "alice",
    "parent": "bitcoins.vrsc",
    "native_coin": "VRSC",
    "primary_raddress": "RaliceAddress"
  }'
```

## 4. Ops checks

Recent failures:

```bash
curl -s "http://localhost:5003/api/registrations/failures?limit=20" \
  -H "X-API-Key: key1"
```

Requeue webhook for a terminal request:

```bash
curl -s -X POST "http://localhost:5003/api/webhook/requeue/<request_id>" \
  -H "X-API-Key: key1"
```

## 5. Backup and restore (PostgreSQL)

Compose stores DB data on volume `idcreate_postgres_data`.

Recommended:

- periodic host-level volume snapshots
- keep at least daily backups
- keep longer retention for operational audits

Example backup command:

```bash
docker compose -f sample.docker-compose.yaml exec -T postgres pg_dump -U idcreate -d idcreate > idcreate_backup.sql
```

## 6. Updating service

```bash
docker compose -f sample.docker-compose.yaml pull
docker compose -f sample.docker-compose.yaml up -d --build
```

Because schema migration is handled in app startup, startup updates are forward-compatible for the current fields.

## 7. Rollback

If a new release misbehaves:

1. Roll back to previous image/tag.
2. Restart API and worker containers.
3. Validate with `/health` and one `GET /api/status/{request_id}` query.

## 8. Alternative worker mode (cron)

If you prefer cron over a containerized worker, run API container only and execute worker on host:

```cron
* * * * * cd /path/to/svc-idcreate && /home/mylo/.local/bin/uv run python worker.py >> /var/log/svc-idcreate-worker.log 2>&1
```

In this mode, ensure host worker uses the same `DATABASE_URL` as API (or the same `REGISTRAR_DB_PATH` only when intentionally running SQLite fallback).
