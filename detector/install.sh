#!/usr/bin/env bash
# ============================================================
# Apple Silicon Detector 安装脚本
# 用于安装 Frigate 的 macOS 原生人体检测器（利用 Neural Engine）
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

INSTALL_DIR="$HOME/Applications/FrigateDetector"
LAUNCHD_PLIST="$SCRIPT_DIR/com.frigate.detector.plist"
LAUNCHD_TARGET="$HOME/Library/LaunchAgents/com.frigate.detector.plist"
LOG_DIR="$HOME/Library/Logs"

# Frigate Apple Silicon Detector 下载地址
# 请根据实际 Frigate 版本更新此 URL
DETECTOR_RELEASE_URL="https://github.com/blakeblackshear/frigate/releases"

echo "============================================"
echo " Frigate Apple Silicon Detector 安装脚本"
echo "============================================"
echo ""

# 1. 检查系统要求
echo "[1/5] 检查系统要求..."

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "错误：此脚本仅支持 macOS"
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
    echo "错误：需要 Apple Silicon (arm64)，当前架构为 $ARCH"
    exit 1
fi

echo "  macOS $(sw_vers -productVersion) on $ARCH - OK"

# 2. 检查是否已安装
echo ""
echo "[2/5] 检查安装状态..."

if [[ -d "$INSTALL_DIR" ]] && ls "$INSTALL_DIR"/*.app &>/dev/null 2>&1; then
    echo "  Apple Silicon Detector 已安装在 $INSTALL_DIR"
    echo "  如需重新安装，请先删除该目录"
    read -p "  是否跳过下载继续配置 launchd？(Y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        exit 0
    fi
    SKIP_DOWNLOAD=true
else
    SKIP_DOWNLOAD=false
fi

# 3. 下载 Detector
if [[ "$SKIP_DOWNLOAD" == "false" ]]; then
    echo ""
    echo "[3/5] 下载 Apple Silicon Detector..."
    echo ""
    echo "  由于 Frigate 的 Apple Silicon Detector 发布方式可能随版本变化，"
    echo "  请手动下载并安装："
    echo ""
    echo "  1. 打开 Frigate Releases 页面："
    echo "     $DETECTOR_RELEASE_URL"
    echo ""
    echo "  2. 找到最新版本中的 Apple Silicon Detector 附件"
    echo "     （通常命名为 frigate-detector-apple-silicon-*.dmg 或 .zip）"
    echo ""
    echo "  3. 下载后将 .app 文件放到以下目录："
    echo "     $INSTALL_DIR/"
    echo ""
    mkdir -p "$INSTALL_DIR"
    echo "  已创建安装目录：$INSTALL_DIR"
    echo ""
    read -p "  放置好 Detector 应用后按回车继续..." -r
fi

# 4. 查找 Detector 可执行文件
echo ""
echo "[4/5] 配置 launchd 自启动..."

# 尝试找到 Detector 的可执行路径
DETECTOR_BIN=""
if ls "$INSTALL_DIR"/*.app/Contents/MacOS/* &>/dev/null 2>&1; then
    DETECTOR_BIN=$(ls "$INSTALL_DIR"/*.app/Contents/MacOS/* 2>/dev/null | head -1)
elif [[ -f "$INSTALL_DIR/frigate-detector" ]]; then
    DETECTOR_BIN="$INSTALL_DIR/frigate-detector"
fi

if [[ -z "$DETECTOR_BIN" ]]; then
    echo "  警告：未找到 Detector 可执行文件"
    echo "  请手动编辑 launchd plist 中的路径"
    DETECTOR_BIN="$INSTALL_DIR/REPLACE_WITH_ACTUAL_PATH"
fi

echo "  Detector 路径：$DETECTOR_BIN"

# 5. 安装 launchd plist
echo ""
echo "[5/5] 安装 launchd 服务..."

# 生成实际的 plist 文件（替换路径占位符）
mkdir -p "$(dirname "$LAUNCHD_TARGET")"

sed "s|__DETECTOR_BIN__|$DETECTOR_BIN|g; s|__LOG_DIR__|$LOG_DIR|g" \
    "$LAUNCHD_PLIST" > "$LAUNCHD_TARGET"

echo "  plist 已安装到：$LAUNCHD_TARGET"

# 加载服务
if launchctl list | grep -q "com.frigate.detector"; then
    echo "  卸载旧服务..."
    launchctl unload "$LAUNCHD_TARGET" 2>/dev/null || true
fi

launchctl load "$LAUNCHD_TARGET"
echo "  服务已加载"

# 验证
sleep 2
if launchctl list | grep -q "com.frigate.detector"; then
    echo ""
    echo "============================================"
    echo " 安装完成！"
    echo "============================================"
    echo ""
    echo "  Detector 已配置为开机自启动，崩溃后会自动重启。"
    echo "  日志位置：$LOG_DIR/frigate-detector.log"
    echo ""
    echo "  管理命令："
    echo "    查看状态：launchctl list | grep frigate"
    echo "    停止服务：launchctl unload $LAUNCHD_TARGET"
    echo "    启动服务：launchctl load $LAUNCHD_TARGET"
else
    echo ""
    echo "警告：服务可能未正确启动，请检查日志："
    echo "  cat $LOG_DIR/frigate-detector.log"
fi
