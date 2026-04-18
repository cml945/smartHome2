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
        detector-install detector-start detector-stop

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
