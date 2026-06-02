# ============================================================
# 智能家居人体位置感知灯控系统 - Makefile
# ============================================================
# 用法：
#   make setup           一键安装（OrbStack、配置生成、启动服务）
#   make up              启动 Frigate 容器
#   make down            停止 Frigate 容器
#   make logs            查看 Frigate 日志
#   make check           系统健康检查
#   make config          重新生成 Frigate 配置
#   make net-test        网络连通性诊断
#   make detector-install 安装 Apple Silicon Detector
#   make detector-start  启动 Detector 服务
#   make detector-stop   停止 Detector 服务
#   make xiaomi-token-refresh 刷新小米摄像头 token 并重启 go2rtc
#   make token-watch-install 安装小米 token 过期监控
#   make dashboard       启动本地 Web 控制台
# ============================================================

SHELL := /bin/bash
PROJECT_DIR := $(shell pwd)
DOCKER_DIR := $(PROJECT_DIR)/docker
COMPOSE := docker compose -f $(DOCKER_DIR)/docker-compose.yml

# 如果存在 override 文件，自动加载
ifneq (,$(wildcard $(DOCKER_DIR)/docker-compose.override.yml))
	COMPOSE += -f $(DOCKER_DIR)/docker-compose.override.yml
endif

.PHONY: help setup up down restart logs check config net-test \
        detector-install detector-start detector-stop \
        xiaomi-token-refresh token-watch-run token-watch-install \
        token-watch-start token-watch-stop token-watch-status \
        dashboard dashboard-open dashboard-status

help: ## 显示帮助信息
	@echo ""
	@echo "可用命令："
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

setup: ## 一键安装（OrbStack、配置生成、Detector、启动服务）
	@bash scripts/setup.sh

up: ## 启动 Frigate 容器
	$(COMPOSE) up -d
	@echo ""
	@echo "Frigate Web UI:  http://localhost:$${FRIGATE_PORT:-8971}"
	@echo "go2rtc Web UI:   http://localhost:$${FRIGATE_GO2RTC_PORT:-1984}"

down: ## 停止 Frigate 容器
	$(COMPOSE) down

restart: ## 重启 Frigate 容器
	$(COMPOSE) restart

logs: ## 查看 Frigate 实时日志
	$(COMPOSE) logs -f

check: ## 运行系统健康检查
	@bash scripts/health-check.sh

config: ## 从 .env + 模板重新生成 Frigate 配置
	@bash scripts/generate-config.sh

net-test: ## 运行网络连通性诊断
	@bash scripts/network-test.sh

detector-install: ## 安装 Apple Silicon Detector
	@bash detector/install.sh

detector-start: ## 启动 Detector 服务（launchd）
	launchctl load ~/Library/LaunchAgents/com.frigate.detector.plist
	@echo "Detector 服务已启动"

detector-stop: ## 停止 Detector 服务（launchd）
	launchctl unload ~/Library/LaunchAgents/com.frigate.detector.plist
	@echo "Detector 服务已停止"

detector-logs: ## 查看 Detector 日志
	@tail -f ~/Library/Logs/frigate-detector.log

xiaomi-token-refresh: ## 刷新小米摄像头 token，写入配置，重启并检查 go2rtc
	@python3 scripts/get_xiaomi_token.py --yes --restart --check

token-watch-run: ## 立即运行一次小米 token 监控
	@bash scripts/xiaomi-token-watch.sh

token-watch-install: ## 安装小米 token 过期监控（launchd，每 10 分钟）
	@chmod +x scripts/xiaomi-token-watch.sh
	@cp go2rtc/com.xiaomi-token-watch.plist ~/Library/LaunchAgents/com.xiaomi-token-watch.plist
	@launchctl unload ~/Library/LaunchAgents/com.xiaomi-token-watch.plist 2>/dev/null || true
	@launchctl load ~/Library/LaunchAgents/com.xiaomi-token-watch.plist
	@echo "小米 token 监控已安装并启动"

token-watch-start: ## 启动小米 token 监控
	launchctl load ~/Library/LaunchAgents/com.xiaomi-token-watch.plist

token-watch-stop: ## 停止小米 token 监控
	launchctl unload ~/Library/LaunchAgents/com.xiaomi-token-watch.plist

token-watch-status: ## 查看小米 token 监控状态
	@launchctl print gui/$$(id -u)/com.xiaomi-token-watch >/dev/null 2>&1 && \
		echo "已安装（launchd 定时任务，每 10 分钟运行一次）" || echo "未安装或未加载"

status: ## 查看所有服务状态概览
	@echo ""
	@echo ">> Docker 容器："
	@$(COMPOSE) ps 2>/dev/null || echo "  （未运行）"
	@echo ""
	@echo ">> Apple Silicon Detector："
	@if pgrep -f "frigate.*detector\|FrigateDetector" > /dev/null 2>&1; then \
		echo "  运行中 (PID: $$(pgrep -f 'frigate.*detector\|FrigateDetector'))"; \
	else \
		echo "  未运行"; \
	fi
	@echo ""

dashboard: ## 启动本地 Web 控制台（仅监听 127.0.0.1:8765）
	@python3 dashboard/server.py

dashboard-open: ## 在浏览器打开本地 Web 控制台
	@open http://127.0.0.1:8765

dashboard-status: ## 输出控制台使用的结构化状态 JSON
	@python3 dashboard/server.py --check
