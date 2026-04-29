#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[phase6] starting staging check with HTTP provisioning adapter"

cd "$REPO_ROOT"
docker compose up -d svc-provisioning

PROVISIONING_ADAPTER_MODE=http \
PROVISIONING_SERVICE_URL=http://127.0.0.1:5055 \
PROVISIONING_RETRY_COUNT=1 \
"$REPO_ROOT/.venv/bin/python" -m pytest tests/test_provisioning_api.py -q

echo "[phase6] staging check passed"
