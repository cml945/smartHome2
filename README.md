# 智能家居人体位置感知灯控系统

基于 Frigate + Home Assistant + 米家生态的「摄像头人体检测 -> 区域判定 -> 自动开灯」系统。

```
小米摄像头 ──P2P──▶ go2rtc ──RTSP──▶ Frigate ──MQTT──▶ HA自动化 ──控制──▶ 米家灯具
(多房间)           (流转发)          (人体检测+区域判定)    (规则引擎)       (蓝牙/WiFi)
```

## 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                   Mac Mini M4 (macOS, 24h 运行)                   │
│                                                                    │
│  ┌─────────────────────────────┐  ┌────────────────────────────┐ │
│  │  macOS 宿主机                 │  │  UTM 虚拟机 (Linux)         │ │
│  │                               │  │                            │ │
│  │  ┌─────────────────────────┐ │  │  ┌──────────────────────┐ │ │
│  │  │  Docker (OrbStack)       │ │  │  │  Home Assistant       │ │ │
│  │  │  ┌────────┐ ┌────────┐  │ │  │  │  - Frigate 集成       │ │ │
│  │  │  │ go2rtc │→│Frigate │──│─│──│─▶│  - ha_xiaomi_home    │ │ │
│  │  │  └────────┘ └────────┘  │ │  │  │  - Mosquitto MQTT    │ │ │
│  │  └─────────────────────────┘ │  │  │  - 自动化规则         │ │ │
│  │                               │  │  └──────────────────────┘ │ │
│  │  ┌─────────────────────────┐ │  └────────────────────────────┘ │
│  │  │  Apple Silicon Detector  │ │                                  │
│  │  │  (YOLOv9, Neural Engine) │ │                                  │
│  │  └─────────────────────────┘ │                                  │
│  └─────────────────────────────┘                                  │
└──────────────────────────────────────────────────────────────────┘
```

## 前置条件

| 项目 | 要求 |
|------|------|
| 硬件 | Mac Mini M4（Apple Silicon），16GB+ 内存 |
| 虚拟化 | UTM 中运行 Home Assistant OS (HAOS) |
| HACS | 已安装 Home Assistant Community Store |
| 米家设备 | 小米摄像头 + 蓝牙中枢网关 + 米家灯具 |
| 网络 | 所有设备在同一局域网内 |

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/cml945/smartHome2.git
cd smartHome2
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写你的实际参数（小米账号、摄像头 IP、HA IP 等）
```

### 3. 一键安装

```bash
make setup
```

此命令会：
- 检查系统环境（macOS、Apple Silicon）
- 引导安装 OrbStack（如未安装）
- 从 `.env` 生成 Frigate 配置
- 安装 Apple Silicon Detector
- 启动 Frigate Docker 容器

> 如遇镜像拉取困难，请确保系统代理已开启。OrbStack 会自动继承 macOS 系统代理设置。

### 4. 配置摄像头

打开 go2rtc WebUI：http://localhost:1984
- 点击 **Add** -> **Xiaomi**
- 用小米账号登录，自动发现摄像头
- 验证每个摄像头的视频流正常

### 5. 绘制检测区域

打开 Frigate WebUI：http://localhost:8971
- 进入每个摄像头的设置页面
- 绘制 Zone（沙发区、餐桌区、书桌区等）
- 验证人体检测效果

### 6. 配置 Home Assistant

**安装集成：**
1. HACS 中搜索并安装 **Frigate Integration**
2. 安装 **ha_xiaomi_home**（小米官方集成），登录小米账号

**导入自动化（二选一）：**
- **方式 A（推荐）：** 将 `homeassistant/packages/smart_presence.yaml` 复制到 HA 的 `config/packages/` 目录，重启 HA
- **方式 B：** 参考 `homeassistant/automations/` 中的模板，在 HA UI 中手动创建自动化

**配置 Frigate Integration：**
- HA 中添加 Frigate 集成时，URL 填写：`http://<Mac的局域网IP>:8971`

### 7. 验证

```bash
make check    # 运行健康检查
make net-test # 网络连通性诊断
```

走进摄像头视角的指定区域 -> 对应灯应该自动亮起；离开后延迟熄灭。

## 项目结构

```
smartHome2/
├── .env.example                  # 环境变量模板
├── Makefile                      # 便捷命令
├── docker/
│   └── docker-compose.yml        # Frigate 容器部署
├── frigate/
│   └── config.example.yml        # Frigate 配置模板
├── homeassistant/
│   ├── automations/              # HA 自动化模板
│   └── packages/                 # HA packages（推荐方式）
├── detector/
│   ├── install.sh                # Detector 安装脚本
│   └── com.frigate.detector.plist # launchd 自启动配置
├── scripts/
│   ├── setup.sh                  # 主安装脚本
│   ├── health-check.sh           # 健康检查
│   ├── network-test.sh           # 网络诊断
│   └── generate-config.sh        # 配置生成
└── docs/                         # 详细文档
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `make setup` | 一键安装 |
| `make up` | 启动 Frigate |
| `make down` | 停止 Frigate |
| `make logs` | 查看日志 |
| `make check` | 健康检查 |
| `make config` | 重新生成配置 |
| `make net-test` | 网络诊断 |
| `make status` | 查看服务状态 |
| `make detector-install` | 安装 Detector |
| `make detector-start` | 启动 Detector |
| `make detector-stop` | 停止 Detector |

## 自定义

### 添加/删除摄像头

1. 编辑 `.env`，添加或删除摄像头的 IP/DID/Model 配置
2. 编辑 `frigate/config.example.yml`，添加或删除对应的 go2rtc stream 和 camera 配置
3. 运行 `make config` 重新生成配置
4. 运行 `make restart` 重启 Frigate

### 调整 Zone 和灯具映射

1. 在 Frigate WebUI 中调整 Zone 坐标
2. 在 HA 中修改对应的自动化规则（entity_id 映射）

### 调整灯光参数

编辑 `homeassistant/packages/smart_presence.yaml` 中各自动化的 `brightness_pct` 和 `color_temp_kelvin` 值。

## 详细文档

- [系统架构设计](docs/architecture.md)
- [网络排障指南](docs/network-troubleshooting.md)
- [Zone 标定说明](docs/zone-calibration.md)

## 性能参考

| 资源 | 预估消耗 | 说明 |
|------|---------|------|
| CPU (M4) | ~15-25% | Frigate 解码 + go2rtc 转发 |
| Neural Engine | ~5-10% | YOLOv9 推理（每路 1FPS） |
| 内存 | ~2-3GB | Frigate + go2rtc 容器 |
| 端到端延迟 | <1s | 从摄像头抓帧到灯具响应 |

## License

MIT
