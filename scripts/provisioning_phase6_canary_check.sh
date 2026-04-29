#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[phase6] running canary contract checks against HTTP provisioning service"

cd "$REPO_ROOT"
docker compose up -d svc-provisioning

PROVISIONING_ADAPTER_MODE=http \
PROVISIONING_SERVICE_URL=http://127.0.0.1:5055 \
PROVISIONING_RETRY_COUNT=1 \
"$REPO_ROOT/.venv/bin/python" -m pytest \
  tests/test_provisioning_http_adapter_contract.py \
  tests/test_provisioning_adapter_selection.py \
  -q

echo "[phase6] canary contract checks passed"
