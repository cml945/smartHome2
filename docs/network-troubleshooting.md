# 网络排障指南

本文档帮助解决 OrbStack (Docker) <-> UTM (Home Assistant) <-> 摄像头之间的网络连通性问题。

## 网络拓扑

```
小米摄像头 ──── P2P/cs2 (局域网) ────▶ go2rtc (:1984)
go2rtc     ──── RTSP (本地回环)  ────▶ Frigate (:8971)
Frigate    ──── MQTT             ────▶ HA Mosquitto (:1883)
HA         ──── MIoT (局域网/云)  ────▶ 蓝牙网关 → 灯具
```

| 通信链路 | 协议 | 预期延迟 |
|---------|------|---------|
| 摄像头 -> go2rtc | Xiaomi P2P | <100ms |
| go2rtc -> Frigate | RTSP | <10ms |
| Frigate 推理 | YOLOv9 | ~10ms |
| Frigate -> HA | MQTT | <50ms |
| HA -> 灯具 | BLE Mesh/WiFi | <500ms |

## 诊断工具

```bash
make net-test  # 自动化网络诊断
```

## 常见问题

### 问题 1：Frigate 容器无法连接 HA 的 MQTT

**现象：** Frigate 日志中出现 MQTT 连接超时

**原因：** OrbStack 容器和 UTM 虚拟机处于不同的虚拟网络中

**解决方案：**

**方案 A（先试）：** OrbStack 容器的出站流量通过 macOS NAT，通常可以直接访问 UTM 桥接模式的 IP。确认 Frigate config 中 `mqtt.host` 填写的是 UTM 虚拟机的局域网 IP（如 `192.168.1.200`）。

**方案 B：** 如果方案 A 不通，在 `docker/docker-compose.override.yml` 中使用 host 网络模式：
```yaml
services:
  frigate:
    network_mode: host
```
注意：使用 host 模式时需要移除 `ports:` 映射。

**方案 C：** 将 HA 也迁移到 OrbStack 中运行（OrbStack 支持运行 Linux 虚拟机），让所有组件在同一网络栈。

### 问题 2：HA 的 Frigate Integration 无法连接 Frigate API

**现象：** HA 中 Frigate 集成配置时提示连接失败

**原因：** HA 在 UTM 虚拟机中，需要通过 Mac 的局域网 IP 访问 Docker 映射的端口

**解决方案：** 在 HA 中配置 Frigate Integration 时，URL 填写：
```
http://<Mac的局域网IP>:8971
```
例如：`http://192.168.1.100:8971`

**不要使用** `localhost` 或 `127.0.0.1`（这指向 UTM VM 自身）。

### 问题 3：Apple Silicon Detector ZeroMQ 连接失败

**现象：** Frigate 日志中显示 detector 不可用

**原因：** Docker 容器需要通过 `host.docker.internal` 访问宿主机的 ZeroMQ 端口

**检查步骤：**

1. 确认 Detector 在 macOS 宿主机上运行：
```bash
pgrep -f "frigate.*detector\|FrigateDetector"
```

2. 确认 ZeroMQ 端口在监听：
```bash
nc -z localhost 5555
```

3. 确认 Docker 容器能解析 `host.docker.internal`：
```bash
docker exec frigate ping -c 1 host.docker.internal
```

4. 确认 Frigate config 中 detector address 使用了 `host.docker.internal:5555`。

### 问题 4：摄像头连接失败

**现象：** go2rtc WebUI 中摄像头显示离线

**排查步骤：**

1. 确认摄像头和 Mac 在同一局域网：
```bash
ping <摄像头IP>
```

2. 确认小米账号信息正确（user_id、region）

3. 首次连接需要互联网来交换加密密钥，确保 Mac 能上网

4. 检查摄像头固件是否为支持的版本（小米智能摄像机 2/3 系列已确认支持）

### 问题 5：Docker 镜像拉取失败

**现象：** `docker pull` 超时或连接被拒

**解决方案：** OrbStack/Docker Desktop 会自动继承 macOS 系统代理设置。确保：

1. macOS 系统偏好设置 -> 网络 -> 代理 已正确配置
2. 或者通过终端设置代理：
```bash
export https_proxy=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890
```

## 端口清单

| 端口 | 服务 | 说明 |
|------|------|------|
| 8971 | Frigate Web UI | NVR 管理界面 |
| 8554 | RTSP Restream | go2rtc 转发的 RTSP 流 |
| 1984 | go2rtc Web UI | 摄像头发现和调试 |
| 8555 | WebRTC | 实时视频流 |
| 5555 | ZeroMQ | Detector 通信端口 |
| 1883 | MQTT | Mosquitto Broker（在 HA 中）|
| 8123 | HA Web UI | Home Assistant 管理界面 |
