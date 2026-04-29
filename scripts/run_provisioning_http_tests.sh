#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cleanup() {
  docker compose -f "$REPO_ROOT/docker-compose.yaml" stop svc-provisioning >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose -f "$REPO_ROOT/docker-compose.yaml" up -d svc-provisioning

PROVISIONING_ADAPTER_MODE=http \
PROVISIONING_SERVICE_URL=http://127.0.0.1:5055 \
PROVISIONING_HTTP_TIMEOUT_SECONDS=10 \
PROVISIONING_RETRY_COUNT=1 \
"$REPO_ROOT/.venv/bin/python" -m pytest \
  tests/test_provisioning_http_adapter_contract.py \
  tests/test_provisioning_adapter_selection.py \
  tests/test_provisioning_golden_vectors.py \
  tests/test_provisioning_api.py \
  -q
