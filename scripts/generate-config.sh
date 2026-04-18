#!/usr/bin/env bash
# ============================================================
# 从 .env + config.example.yml 生成 Frigate 实际配置文件
# 用法：./scripts/generate-config.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

ENV_FILE="$PROJECT_DIR/.env"
TEMPLATE_FILE="$PROJECT_DIR/frigate/config.example.yml"
OUTPUT_FILE="$PROJECT_DIR/frigate/config.yml"

# 检查 .env 是否存在
if [[ ! -f "$ENV_FILE" ]]; then
    echo "错误：找不到 .env 文件"
    echo "请先复制模板：cp .env.example .env，然后填写实际配置值"
    exit 1
fi

# 检查模板文件是否存在
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "错误：找不到 Frigate 配置模板 $TEMPLATE_FILE"
    exit 1
fi

# 加载 .env 文件
set -a
source "$ENV_FILE"
set +a

# 收集模板中所有需要的变量
VARS=$(grep -oE '\$\{[A-Z_]+\}' "$TEMPLATE_FILE" | sort -u | sed 's/\${\(.*\)}/\1/')

# 检查是否有未设置的变量
MISSING=()
for VAR in $VARS; do
    if [[ -z "${!VAR:-}" ]]; then
        MISSING+=("$VAR")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "警告：以下变量在 .env 中未设置或为空："
    for VAR in "${MISSING[@]}"; do
        echo "  - $VAR"
    done
    echo ""
    read -p "是否继续生成配置？(y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 使用 envsubst 生成配置
envsubst < "$TEMPLATE_FILE" > "$OUTPUT_FILE"

echo "Frigate 配置已生成：$OUTPUT_FILE"
echo ""
echo "注意事项："
echo "  1. Zone 坐标仅为示例，请在 Frigate Web UI 中根据实际画面调整"
echo "  2. 如果摄像头数量与模板不同，请手动编辑 $OUTPUT_FILE"
echo "  3. config.yml 已被 .gitignore 忽略，不会提交到 Git"
