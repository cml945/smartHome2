#!/usr/bin/env bash
# ============================================================
# Xiaomi token monitor for go2rtc
# Checks for fresh "401 Unauthorized" errors and notifies HA once
# until the error disappears.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"
GO2RTC_LOG="$PROJECT_DIR/logs/go2rtc.log"
STATE_FILE="$PROJECT_DIR/logs/xiaomi-token-watch.state"
WATCH_LOG="$PROJECT_DIR/logs/xiaomi-token-watch.log"

mkdir -p "$PROJECT_DIR/logs"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$WATCH_LOG"
}

notify_ha() {
    local title="$1"
    local message="$2"

    if [[ ! -f "$ENV_FILE" ]]; then
        log "HA notify skipped: .env not found"
        return 0
    fi

    # shellcheck disable=SC1090
    source "$ENV_FILE"

    if [[ -z "${HA_IP:-}" || -z "${HA_TOKEN:-}" || "$HA_TOKEN" == "your_ha_long_lived_access_token" ]]; then
        log "HA notify skipped: HA_IP/HA_TOKEN not configured"
        return 0
    fi

    curl --noproxy '*' -fsS -X POST "http://${HA_IP}:8123/api/services/persistent_notification/create" \
        -H "Authorization: Bearer ${HA_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"title\":\"${title}\",\"message\":\"${message}\"}" >/dev/null || \
        log "HA notify failed"
}

if ! curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:1984/api/streams >/dev/null; then
    if [[ "$(cat "$STATE_FILE" 2>/dev/null || true)" != "go2rtc_down" ]]; then
        log "go2rtc API unreachable"
        notify_ha "go2rtc 离线" "go2rtc Web/API 无法访问，摄像头检测可能已暂停。"
        echo "go2rtc_down" > "$STATE_FILE"
    fi
    exit 0
fi

if [[ ! -f "$GO2RTC_LOG" ]]; then
    log "go2rtc log not found: $GO2RTC_LOG"
    exit 0
fi

if tail -n 300 "$GO2RTC_LOG" | grep -q '401 Unauthorized'; then
    if [[ "$(cat "$STATE_FILE" 2>/dev/null || true)" != "xiaomi_401" ]]; then
        log "Xiaomi token appears expired: 401 Unauthorized found"
        notify_ha "小米摄像头 token 可能已过期" "go2rtc 日志出现 401 Unauthorized。请在 Mac 上运行：cd ${PROJECT_DIR} && make xiaomi-token-refresh"
        echo "xiaomi_401" > "$STATE_FILE"
    fi
else
    if [[ -f "$STATE_FILE" ]]; then
        log "Xiaomi token/go2rtc alert cleared"
        rm -f "$STATE_FILE"
    fi
fi
