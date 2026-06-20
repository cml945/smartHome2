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

LAST_START_LINE="$(grep -n 'INF go2rtc platform=' "$GO2RTC_LOG" | tail -n 1 | cut -d: -f1 || true)"
if [[ -n "$LAST_START_LINE" ]]; then
    RECENT_GO2RTC_LOG="$(sed -n "${LAST_START_LINE},\$p" "$GO2RTC_LOG")"
else
    RECENT_GO2RTC_LOG="$(tail -n 1000 "$GO2RTC_LOG")"
fi

if grep -q '401 Unauthorized' <<<"$RECENT_GO2RTC_LOG"; then
    if [[ "$(cat "$STATE_FILE" 2>/dev/null || true)" != "xiaomi_401" ]]; then
        log "Xiaomi token appears expired: 401 Unauthorized found"
        notify_ha "小米摄像头 token 可能已过期" "go2rtc 日志出现 401 Unauthorized。请在 Mac 上运行：cd ${PROJECT_DIR} && make xiaomi-token-refresh"
        echo "xiaomi_401" > "$STATE_FILE"
    fi
elif grep -q 'permit deny' <<<"$RECENT_GO2RTC_LOG"; then
    if [[ "$(cat "$STATE_FILE" 2>/dev/null || true)" != "xiaomi_permit_deny" ]]; then
        STREAMS="$(grep 'permit deny' <<<"$RECENT_GO2RTC_LOG" | sed -n 's/.*stream=\([^ ]*\).*/\1/p' | sort -u | tr '\n' ' ')"
        log "Xiaomi stream permission/config error: ${STREAMS:-unknown stream}"
        notify_ha "小米摄像头权限或配置异常" "go2rtc 日志出现 permit deny：${STREAMS:-unknown stream}。请检查 stream 的 userId、IP、DID、model，以及摄像头是否属于当前小米账号。"
        echo "xiaomi_permit_deny" > "$STATE_FILE"
    fi
else
    if [[ -f "$STATE_FILE" ]]; then
        log "Xiaomi token/go2rtc alert cleared"
        rm -f "$STATE_FILE"
    fi
fi
