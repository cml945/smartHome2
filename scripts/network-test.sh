#!/usr/bin/env bash
# ============================================================
# 网络连通性诊断脚本
# 测试 OrbStack <-> UTM <-> 摄像头之间的网络连通性
# 用法：./scripts/network-test.sh 或 make net-test
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
pass()  { echo -e "  ${GREEN}[PASS]${NC} $*"; }
fail()  { echo -e "  ${RED}[FAIL]${NC} $*"; }
warn()  { echo -e "  ${YELLOW}[WARN]${NC} $*"; }

# 加载 .env
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
else
    echo "警告：.env 文件不存在，使用默认值"
fi

HA_IP="${HA_IP:-192.168.1.200}"
MQTT_PORT="${MQTT_PORT:-1883}"
MAC_HOST_IP="${MAC_HOST_IP:-$(ipconfig getifaddr en0 2>/dev/null || echo '未知')}"
FRIGATE_PORT="${FRIGATE_PORT:-8971}"
DETECTOR_ZMQ_PORT="${DETECTOR_ZMQ_PORT:-5555}"

echo ""
echo "============================================"
echo " 网络连通性诊断"
echo "============================================"
echo ""
echo " Mac 宿主机 IP: $MAC_HOST_IP"
echo " HA (UTM VM) IP: $HA_IP"
echo ""

# ===== 1. Mac -> 摄像头 =====
echo ">> 测试 1：Mac 宿主机 -> 摄像头"
for VAR_PREFIX in CAM_LIVING_ROOM CAM_BEDROOM CAM_STUDY; do
    IP_VAR="${VAR_PREFIX}_IP"
    IP="${!IP_VAR:-}"
    if [[ -n "$IP" && "$IP" != "192.168.1."* || -n "$IP" ]]; then
        if ping -c 2 -W 2 "$IP" &>/dev/null; then
            RTT=$(ping -c 1 -W 2 "$IP" 2>/dev/null | grep "time=" | sed 's/.*time=\([^ ]*\).*/\1/')
            pass "$VAR_PREFIX ($IP) - ${RTT}ms"
        else
            fail "$VAR_PREFIX ($IP) - 不可达"
        fi
    fi
done
echo ""

# ===== 2. Mac -> HA (UTM VM) =====
echo ">> 测试 2：Mac 宿主机 -> Home Assistant (UTM)"
if ping -c 2 -W 3 "$HA_IP" &>/dev/null; then
    RTT=$(ping -c 1 -W 2 "$HA_IP" 2>/dev/null | grep "time=" | sed 's/.*time=\([^ ]*\).*/\1/')
    pass "ping $HA_IP - ${RTT}ms"
else
    fail "ping $HA_IP - 不可达"
    warn "请检查 UTM 虚拟机是否使用桥接网络模式"
fi

# HA API
if curl -s -o /dev/null -w "%{http_code}" "http://$HA_IP:8123/api/" -m 5 2>/dev/null | grep -qE "200|401|403"; then
    pass "HA API ($HA_IP:8123) - 可达"
else
    fail "HA API ($HA_IP:8123) - 不可达"
fi

# MQTT
if nc -z -w 3 "$HA_IP" "$MQTT_PORT" 2>/dev/null; then
    pass "MQTT ($HA_IP:$MQTT_PORT) - 可达"
else
    fail "MQTT ($HA_IP:$MQTT_PORT) - 不可达"
    warn "请确认 HA 的 Mosquitto Add-on 已安装并启动"
fi
echo ""

# ===== 3. Docker 容器 -> HA =====
echo ">> 测试 3：Docker 容器 (Frigate) -> Home Assistant"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "frigate"; then
    # ping
    if docker exec frigate ping -c 2 -W 3 "$HA_IP" &>/dev/null 2>&1; then
        pass "容器 -> HA ping ($HA_IP) - 可达"
    else
        fail "容器 -> HA ping ($HA_IP) - 不可达"
        warn "OrbStack 容器无法访问 UTM VM IP"
        warn "方案 A：尝试 docker-compose.override.yml 中使用 network_mode: host"
        warn "方案 B：将 HA 迁移到 OrbStack（同一网络栈）"
    fi

    # MQTT
    if docker exec frigate sh -c "echo > /dev/tcp/$HA_IP/$MQTT_PORT" &>/dev/null 2>&1; then
        pass "容器 -> MQTT ($HA_IP:$MQTT_PORT) - 可达"
    else
        # 备用检测方式
        if docker exec frigate nc -z -w 3 "$HA_IP" "$MQTT_PORT" &>/dev/null 2>&1; then
            pass "容器 -> MQTT ($HA_IP:$MQTT_PORT) - 可达"
        else
            fail "容器 -> MQTT ($HA_IP:$MQTT_PORT) - 不可达"
        fi
    fi

    # host.docker.internal 解析
    if docker exec frigate ping -c 1 -W 2 host.docker.internal &>/dev/null 2>&1; then
        pass "容器 -> host.docker.internal - 可解析"
    else
        fail "容器 -> host.docker.internal - 不可解析"
        warn "Detector ZeroMQ 连接可能失败"
    fi
else
    warn "Frigate 容器未运行，跳过容器网络测试"
fi
echo ""

# ===== 4. ZeroMQ 端口 =====
echo ">> 测试 4：Apple Silicon Detector ZeroMQ 端口"
if nc -z -w 2 localhost "$DETECTOR_ZMQ_PORT" 2>/dev/null; then
    pass "ZeroMQ 端口 (localhost:$DETECTOR_ZMQ_PORT) - 监听中"
else
    fail "ZeroMQ 端口 (localhost:$DETECTOR_ZMQ_PORT) - 未监听"
    warn "Apple Silicon Detector 可能未运行"
fi
echo ""

# ===== 5. HA -> Frigate（反向访问） =====
echo ">> 测试 5：HA Frigate Integration 反向访问路径"
info "HA 的 Frigate Integration 需要访问 Frigate API"
info "请在 HA 中配置 Frigate URL 为："
echo ""
echo "    http://$MAC_HOST_IP:$FRIGATE_PORT"
echo ""
info "（使用 Mac 的局域网 IP + Frigate 的映射端口）"
echo ""

# ===== 汇总 =====
echo "============================================"
echo " 诊断完成"
echo "============================================"
echo ""
echo " 如果所有测试通过，系统网络配置正确。"
echo ""
echo " 常见问题："
echo "   - 容器无法访问 UTM VM：尝试 network_mode: host"
echo "   - MQTT 不通：检查 HA Mosquitto Add-on 配置"
echo "   - 摄像头不通：确认摄像头和 Mac 在同一局域网"
echo "   - 镜像拉取失败：开启系统代理后重试"
echo ""
