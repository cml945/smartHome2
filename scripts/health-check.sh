#!/usr/bin/env bash
# ============================================================
# 系统健康检查脚本
# 检查所有组件的运行状态
# 用法：./scripts/health-check.sh 或 make check
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

check_pass() { echo -e "  ${GREEN}[PASS]${NC} $*"; ((PASS++)); }
check_fail() { echo -e "  ${RED}[FAIL]${NC} $*"; ((FAIL++)); }
check_warn() { echo -e "  ${YELLOW}[WARN]${NC} $*"; ((WARN++)); }

# 加载 .env（如果存在）
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

echo ""
echo "============================================"
echo " 智能灯控系统 - 健康检查"
echo "============================================"
echo ""

# 1. Docker 环境
echo ">> Docker 环境"
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    check_pass "Docker daemon 运行中"
else
    check_fail "Docker daemon 未运行"
fi
echo ""

# 2. Frigate 容器
echo ">> Frigate 容器"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "frigate"; then
    check_pass "Frigate 容器运行中"

    # 检查容器健康状态
    STATUS=$(docker inspect --format='{{.State.Status}}' frigate 2>/dev/null || echo "unknown")
    if [[ "$STATUS" == "running" ]]; then
        check_pass "容器状态：$STATUS"
    else
        check_fail "容器状态：$STATUS"
    fi
else
    check_fail "Frigate 容器未运行（运行 make up 启动）"
fi
echo ""

# 3. Frigate Web UI
echo ">> Frigate Web UI"
FRIGATE_PORT="${FRIGATE_PORT:-8971}"
if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$FRIGATE_PORT/api/version" 2>/dev/null | grep -q "200"; then
    FRIGATE_VER=$(curl -s "http://localhost:$FRIGATE_PORT/api/version" 2>/dev/null || echo "未知")
    check_pass "Frigate API 响应正常 (版本: $FRIGATE_VER)"
else
    check_fail "Frigate API 无响应 (http://localhost:$FRIGATE_PORT)"
fi
echo ""

# 4. go2rtc Web UI
echo ">> go2rtc"
GO2RTC_PORT="${FRIGATE_GO2RTC_PORT:-1984}"
if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$GO2RTC_PORT" 2>/dev/null | grep -q "200\|301\|302"; then
    check_pass "go2rtc WebUI 可访问 (http://localhost:$GO2RTC_PORT)"
else
    check_fail "go2rtc WebUI 无响应"
fi
echo ""

# 5. Apple Silicon Detector
echo ">> Apple Silicon Detector"
if pgrep -f "frigate.*detector\|FrigateDetector\|apple.*silicon.*detect" &>/dev/null; then
    check_pass "Detector 进程运行中"
elif launchctl list 2>/dev/null | grep -q "com.frigate.detector"; then
    check_warn "Detector 已配置 launchd 但进程未运行"
else
    check_fail "Detector 未运行（运行 make detector-install 安装）"
fi
echo ""

# 6. MQTT 连接
echo ">> MQTT Broker"
HA_IP="${HA_IP:-192.168.1.200}"
MQTT_PORT="${MQTT_PORT:-1883}"
if nc -z -w 3 "$HA_IP" "$MQTT_PORT" 2>/dev/null; then
    check_pass "MQTT Broker 可达 ($HA_IP:$MQTT_PORT)"
else
    check_fail "MQTT Broker 不可达 ($HA_IP:$MQTT_PORT)"
fi
echo ""

# 7. Home Assistant
echo ">> Home Assistant"
if curl -s -o /dev/null -w "%{http_code}" "http://$HA_IP:8123/api/" 2>/dev/null | grep -qE "200|401|403"; then
    check_pass "Home Assistant API 可达 (http://$HA_IP:8123)"
else
    check_fail "Home Assistant 不可达 (http://$HA_IP:8123)"
fi
echo ""

# 8. 摄像头连通性（仅检查 IP 是否可达）
echo ">> 摄像头网络"
for VAR_PREFIX in CAM_LIVING_ROOM CAM_BEDROOM CAM_STUDY; do
    IP_VAR="${VAR_PREFIX}_IP"
    IP="${!IP_VAR:-}"
    if [[ -n "$IP" ]]; then
        if ping -c 1 -W 2 "$IP" &>/dev/null; then
            check_pass "$VAR_PREFIX ($IP) 可达"
        else
            check_fail "$VAR_PREFIX ($IP) 不可达"
        fi
    fi
done
echo ""

# 9. 存储空间
echo ">> 存储"
STORAGE="${FRIGATE_STORAGE_PATH:-/Users/Shared/frigate-storage}"
if [[ -d "$STORAGE" ]]; then
    AVAIL=$(df -h "$STORAGE" | tail -1 | awk '{print $4}')
    check_pass "存储目录可用：$STORAGE（剩余 $AVAIL）"
else
    check_warn "存储目录不存在：$STORAGE"
fi
echo ""

# 汇总
echo "============================================"
echo -e " 结果：${GREEN}$PASS 通过${NC}  ${RED}$FAIL 失败${NC}  ${YELLOW}$WARN 警告${NC}"
echo "============================================"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
