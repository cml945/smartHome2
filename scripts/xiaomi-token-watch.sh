#!/usr/bin/env bash
# Existing launchd / dashboard entry point for Xiaomi session recovery.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/../.env" ]]; then
    set -a
    source "$SCRIPT_DIR/../.env"
    set +a
fi
exec /usr/bin/python3 "$SCRIPT_DIR/xiaomi_token_watch.py"
