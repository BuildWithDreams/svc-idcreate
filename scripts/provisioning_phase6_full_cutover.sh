#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[phase6] full cutover validation (HTTP adapter default workflow)"

"$REPO_ROOT/scripts/run_provisioning_http_tests.sh"

echo "[phase6] full cutover validation passed"
