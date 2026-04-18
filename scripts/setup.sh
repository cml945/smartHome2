#!/usr/bin/env bash
# ============================================================
# 智能家居灯控系统 - 主安装脚本
# 用法：./scripts/setup.sh 或 make setup
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo ""
echo "============================================"
echo " 智能家居人体位置感知灯控系统 - 安装向导"
echo "============================================"
echo ""

# ===== 1. 前置检查 =====
info "步骤 1/6：前置检查..."

# macOS 检查
if [[ "$(uname -s)" != "Darwin" ]]; then
    error "此系统仅支持 macOS"
    exit 1
fi
ok "macOS $(sw_vers -productVersion)"

# Apple Silicon 检查
if [[ "$(uname -m)" != "arm64" ]]; then
    error "需要 Apple Silicon (arm64)"
    exit 1
fi
ok "Apple Silicon ($(uname -m))"

# .env 检查
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    warn ".env 文件不存在"
    echo "  请先复制模板并填写配置："
    echo "  cp .env.example .env"
    echo "  然后编辑 .env 填写你的实际参数"
    exit 1
fi
ok ".env 配置文件已就绪"

echo ""

# ===== 2. OrbStack / Docker 检查 =====
info "步骤 2/6：检查 Docker 环境..."

if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version 2>/dev/null || echo "未知")
    ok "Docker 已安装：$DOCKER_VERSION"
elif command -v orbstack &>/dev/null; then
    ok "OrbStack 已安装"
else
    warn "未检测到 Docker 或 OrbStack"
    echo ""
    echo "  推荐安装 OrbStack（轻量级 Docker 运行时，针对 Apple Silicon 优化）："
    echo ""
    echo "  方式 A（Homebrew）：brew install orbstack"
    echo "  方式 B（官网下载）：https://orbstack.dev/download"
    echo ""
    echo "  安装完成后请重新运行此脚本。"
    echo ""
    echo "  提示：如果遇到下载困难，请确保系统代理已开启。"
    echo "        OrbStack/Docker Desktop 会自动继承 macOS 系统代理设置。"
    exit 1
fi

# 检查 Docker daemon 是否运行
if ! docker info &>/dev/null 2>&1; then
    error "Docker daemon 未运行，请启动 OrbStack 或 Docker Desktop"
    exit 1
fi
ok "Docker daemon 运行中"

echo ""

# ===== 3. 生成 Frigate 配置 =====
info "步骤 3/6：生成 Frigate 配置..."

"$SCRIPT_DIR/generate-config.sh"
ok "Frigate 配置已生成"

echo ""

# ===== 4. Apple Silicon Detector =====
info "步骤 4/6：检查 Apple Silicon Detector..."

DETECTOR_INSTALLED=false
if pgrep -f "frigate.*detector" &>/dev/null || pgrep -f "FrigateDetector" &>/dev/null; then
    ok "Apple Silicon Detector 已在运行"
    DETECTOR_INSTALLED=true
elif launchctl list 2>/dev/null | grep -q "com.frigate.detector"; then
    ok "Apple Silicon Detector 已配置（launchd）"
    DETECTOR_INSTALLED=true
else
    warn "Apple Silicon Detector 未安装"
    echo ""
    read -p "  是否现在安装 Apple Silicon Detector？(Y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        "$SCRIPT_DIR/../detector/install.sh"
        DETECTOR_INSTALLED=true
    else
        warn "跳过 Detector 安装，Frigate 将无法进行 AI 检测"
        echo "  稍后可运行：make detector-install"
    fi
fi

echo ""

# ===== 5. 创建存储目录 =====
info "步骤 5/6：准备存储目录..."

source "$PROJECT_DIR/.env"
STORAGE="${FRIGATE_STORAGE_PATH:-/Users/Shared/frigate-storage}"
mkdir -p "$STORAGE"
ok "存储目录已就绪：$STORAGE"

echo ""

# ===== 6. 启动 Docker 服务 =====
info "步骤 6/6：启动 Frigate..."

echo "  提示：如遇镜像拉取困难，请确保系统代理已开启。"
echo ""

cd "$PROJECT_DIR/docker"
docker compose up -d

sleep 5

if docker compose ps | grep -q "running"; then
    ok "Frigate 容器已启动"
else
    error "Frigate 容器启动失败，请查看日志：make logs"
    exit 1
fi

echo ""

# ===== 安装完成 =====
echo "============================================"
echo -e " ${GREEN}安装完成！${NC}"
echo "============================================"
echo ""
echo " 后续操作："
echo ""
echo " 1. 打开 go2rtc WebUI 配置摄像头："
echo "    http://localhost:${FRIGATE_GO2RTC_PORT:-1984}"
echo "    -> 点击 Add -> Xiaomi -> 登录小米账号"
echo ""
echo " 2. 打开 Frigate WebUI 绘制检测区域："
echo "    http://localhost:${FRIGATE_PORT:-8971}"
echo "    -> 在每个摄像头视图中绘制 Zone"
echo ""
echo " 3. 在 Home Assistant 中配置集成："
echo "    a. HACS 安装 Frigate Integration"
echo "    b. 确认 ha_xiaomi_home 集成已安装"
echo "    c. 导入自动化规则（见 homeassistant/ 目录）"
echo ""
echo " 4. 运行健康检查："
echo "    make check"
echo ""
echo " 常用命令："
echo "    make up     - 启动服务"
echo "    make down   - 停止服务"
echo "    make logs   - 查看日志"
echo "    make check  - 健康检查"
echo ""
