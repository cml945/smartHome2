## 智能家居「人体位置感知自动灯控」系统架构设计

### 一、方案概述

本方案基于你现有的 Mac Mini M4 + UTM Home Assistant + 米家生态，构建一套「摄像头抓帧 → 人体检测 → 区域判定 → 自动开灯」的实时联动系统。核心思路是让 Frigate NVR 承担视频流接入和人体检测的工作，Home Assistant 负责自动化联动和设备控制，充分利用成熟的社区方案，降低开发和维护成本。

整体数据流如下：

```
小米摄像头 ──P2P协议──▶ go2rtc ──RTSP──▶ Frigate ──Zone事件──▶ HA自动化 ──控制──▶ 米家灯具
(多房间)               (流转发)          (人体检测+区域判定)        (规则引擎)         (蓝牙/WiFi)
```

---

### 二、系统架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Mac Mini M4 (macOS, 24h 运行)                     │
│                                                                          │
│  ┌──────────────────────────────────┐  ┌─────────────────────────────┐  │
│  │  macOS 宿主机                      │  │  UTM 虚拟机 (Linux)          │  │
│  │                                    │  │                             │  │
│  │  ┌──────────────────────────────┐ │  │  ┌───────────────────────┐ │  │
│  │  │  Docker (OrbStack)            │ │  │  │  Home Assistant        │ │  │
│  │  │                               │ │  │  │                       │ │  │
│  │  │  ┌─────────┐  ┌───────────┐  │ │  │  │  - Frigate 集成        │ │  │
│  │  │  │ go2rtc   │  │ Frigate   │  │ │  │  │  - Xiaomi Miot Auto   │ │  │
│  │  │  │ (流转发)  │→│ (NVR+检测) │──│─│──│─▶│  - 自动化规则          │ │  │
│  │  │  └─────────┘  └───────────┘  │ │  │  │  - MQTT Broker        │ │  │
│  │  └──────────────────────────────┘ │  │  └───────────────────────┘ │  │
│  │                                    │  │            │               │  │
│  │  ┌──────────────────────────────┐ │  └────────────│───────────────┘  │
│  │  │  Apple Silicon Detector       │ │               │                  │
│  │  │  (YOLOv9, 利用 Neural Engine)  │ │               │                  │
│  │  └──────────────────────────────┘ │               │                  │
│  └──────────────────────────────────┘               │                  │
└──────────────────────────────────────────────────────│──────────────────┘
                                                       │
                         ┌─────────────────────────────┤
                         ▼                             ▼
                  ┌─────────────┐              ┌─────────────┐
                  │ 小米摄像头×N  │              │ 米家灯具×N    │
                  │ (局域网P2P)  │              │ (蓝牙网关控制) │
                  └─────────────┘              └─────────────┘
```

---

### 三、核心组件详解

#### 3.1 视频流接入层：go2rtc

go2rtc 是 Frigate 内置的流媒体服务，原生支持小米摄像头的 `xiaomi://` 协议，无需刷固件或提取 RTSP Token。

**工作原理：** go2rtc 通过小米的 P2P 协议（cs2+udp）直接连接摄像头获取视频流（与米家 App 的连接方式相同），然后在局域网内转发为标准 RTSP 流供 Frigate 消费。

**配置示例：**

```yaml
# go2rtc 配置（集成在 Frigate config 中）
go2rtc:
  streams:
    living_room_cam:
      - xiaomi://USER_ID:cn@192.168.1.101?did=DEVICE_ID_1&model=isa.camera.hlc7
    bedroom_cam:
      - xiaomi://USER_ID:cn@192.168.1.102?did=DEVICE_ID_2&model=isa.camera.hlc7
    study_cam:
      - xiaomi://USER_ID:cn@192.168.1.103?did=DEVICE_ID_3&model=isa.camera.hlc7
```

**获取连接参数的方法：** 启动 go2rtc 后打开其 WebUI（默认 `http://localhost:1984`），点击 Add → Xiaomi，用小米账号登录后会自动发现所有摄像头并填充 user_id、did、model 等参数。

**注意事项：**
- 首次连接需要互联网来交换加密密钥，之后视频流走局域网
- 小米智能摄像机 2/3 系列属于已确认支持的型号
- 支持 H.265/H.264 编码，建议使用默认的 HD 子流

#### 3.2 人体检测层：Frigate + Apple Silicon Detector

Frigate 是目前 HA 生态中最成熟的 NVR 方案，从 0.17 版本开始较好地支持了 Apple Silicon。

**检测架构：** 由于 Docker 容器无法直接访问 M4 的 Neural Engine，Frigate 采用了一个分体式设计——Frigate 本体运行在 Docker 中，而检测模型通过一个独立的 macOS 原生应用（Apple Silicon Detector）运行在宿主机上，两者通过 ZeroMQ 通信。需要注意的是，Apple Silicon Detector 作为一个独立进程运行在 macOS 上，建议通过 launchd 配置开机自启和崩溃自动重启，确保 24h 稳定运行。

**性能表现：** 使用 YOLOv9-tiny 320px 模型，在 M4 上单次推理约 8-10ms，完全满足多路摄像头 1FPS 的检测需求（理论上 M4 可以轻松支撑 8+ 路摄像头）。

**区域（Zone）配置：** Frigate 原生支持在摄像头画面上划定多个 Zone，当检测到的 person 进入/离开某个 Zone 时，会生成对应事件并通过 MQTT 推送给 HA。

```yaml
# Frigate 配置示例
mqtt:
  host: 192.168.1.200  # HA 的 IP（UTM 虚拟机）
  port: 1883

detectors:
  apple_silicon:
    type: apple_silicon
    model:
      path: yolov9-320-t  # 推荐模型，~10ms推理

cameras:
  living_room:
    ffmpeg:
      inputs:
        - path: rtsp://127.0.0.1:8554/living_room_cam
          roles: ["detect"]
    detect:
      width: 1280
      height: 720
      fps: 1  # 每秒检测一次，符合你的需求
    zones:
      sofa_area:
        coordinates: 0.0,0.5,0.4,0.5,0.4,1.0,0.0,1.0
      dining_area:
        coordinates: 0.4,0.3,0.8,0.3,0.8,0.9,0.4,0.9
      entrance:
        coordinates: 0.8,0.0,1.0,0.0,1.0,0.7,0.8,0.7
    objects:
      track:
        - person  # 只检测人

  bedroom:
    ffmpeg:
      inputs:
        - path: rtsp://127.0.0.1:8554/bedroom_cam
          roles: ["detect"]
    detect:
      width: 1280
      height: 720
      fps: 1
    zones:
      bed_area:
        coordinates: 0.0,0.0,0.6,0.0,0.6,0.8,0.0,0.8
      desk_area:
        coordinates: 0.6,0.0,1.0,0.0,1.0,0.8,0.6,0.8
    objects:
      track:
        - person
```

Zone 坐标是归一化的 (0.0~1.0)，在 Frigate 的 Web UI 中可以直接拖拽绘制，非常直观。

#### 3.3 自动化引擎层：Home Assistant

HA 在这套系统中承担两个角色：接收 Frigate 的区域事件、控制米家灯具。

**Frigate 集成：** 通过 HACS 安装 Frigate Integration，它会自动在 HA 中创建 binary_sensor（区域占用状态）和 event trigger（区域进出事件），可以直接用在自动化中。实体命名规则为 `binary_sensor.<摄像头名>_<区域名>_<对象类型>_occupancy`，例如 `binary_sensor.living_room_sofa_area_person_occupancy`。

**小米设备集成（推荐 ha_xiaomi_home）：** 小米官方已推出 Home Assistant 集成 `ha_xiaomi_home`（GitHub: XiaoMi/ha_xiaomi_home），由小米团队维护，2026 年仍在活跃更新（最新 v0.4.7）。推荐优先使用它来控制灯具，灯具会被暴露为标准的 `light.xxx` 实体，支持开关、亮度、色温等控制。如果某些设备不被官方集成支持，可以补充安装社区版 `hass-xiaomi-miot` 作为兜底。注意：虽然这些集成也能创建摄像头实体，但视频流功能不可靠，视频流统一由 go2rtc/Frigate 处理。

**自动化规则示例：**

```yaml
# HA automation: 客厅沙发区有人 → 开沙发区灯
automation:
  - alias: "客厅沙发区灯光联动"
    trigger:
      - platform: state
        entity_id: binary_sensor.living_room_sofa_area_person_occupancy
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.living_room_sofa_lamp
        data:
          brightness_pct: 80
          color_temp_kelvin: 4000

  - alias: "客厅沙发区无人关灯"
    trigger:
      - platform: state
        entity_id: binary_sensor.living_room_sofa_area_person_occupancy
        to: "off"
        for:
          minutes: 3  # 离开3分钟后关灯，避免频繁开关
    action:
      - service: light.turn_off
        target:
          entity_id: light.living_room_sofa_lamp

  - alias: "卧室书桌区灯光联动"
    trigger:
      - platform: state
        entity_id: binary_sensor.bedroom_desk_area_person_occupancy
        to: "on"
    condition:
      - condition: numeric_state
        entity_id: sensor.living_room_illuminance  # 可选：只在光线暗时开灯
        below: 200
    action:
      - service: light.turn_on
        target:
          entity_id: light.bedroom_desk_lamp
```

---

### 四、网络拓扑与通信协议

```
小米摄像头 ──── P2P/cs2 (局域网) ────▶ go2rtc (:1984)
go2rtc     ──── RTSP (本地回环)  ────▶ Frigate (:8971)
Frigate    ──── MQTT             ────▶ HA Mosquitto Broker (:1883)
HA         ──── Xiaomi MIoT (局域网/云) ──▶ 蓝牙网关 ──▶ 米家灯具
```

| 通信链路 | 协议 | 延迟 | 备注 |
|---------|------|------|------|
| 摄像头 → go2rtc | Xiaomi P2P | <100ms | 局域网直连 |
| go2rtc → Frigate | RTSP | <10ms | 本机通信 |
| Frigate 推理 | YOLOv9 | ~10ms | Apple Neural Engine |
| Frigate → HA | MQTT | <50ms | 局域网 |
| HA → 灯具 | BLE Mesh/WiFi | <500ms | 经蓝牙网关 |
| **端到端延迟** | | **<1s** | 满足实时性需求 |

---

### 五、部署步骤概览

**第一步：macOS 宿主机准备**

1. 安装 OrbStack（轻量 Docker 运行时，比 Docker Desktop 更省资源）
2. 下载并运行 Apple Silicon Detector（Frigate 的 macOS 原生检测器）
3. 通过 Docker Compose 部署 Frigate + go2rtc

**第二步：go2rtc 接入摄像头**

1. 打开 go2rtc WebUI，使用小米账号登录
2. 自动发现并添加所有摄像头
3. 验证每个摄像头的 RTSP 流可以正常播放

**第三步：Frigate 配置区域检测**

1. 配置各摄像头的检测参数（分辨率、帧率）
2. 在 Frigate Web UI 中为每个摄像头绘制 Zone（沙发区、餐桌区、书桌区等）
3. 启用 person 检测，验证检测效果

**第四步：Home Assistant 集成**

1. HACS 安装 Frigate Integration，配置 MQTT 连接
2. HACS 安装 ha_xiaomi_home（小米官方集成），登录小米账号
3. 确认 Frigate 的 zone occupancy sensor 和米家灯具实体都正常出现

**第五步：编写自动化规则**

1. 为每个 zone-灯具 对创建自动化
2. 加入延迟关灯逻辑（避免人体微小移动导致灯频繁开关）
3. 可选：加入光照条件判断、时间段判断、勿扰模式等

**第六步：调优**

1. 调整 Zone 边界，减少误触发
2. 调整检测置信度阈值（Frigate 默认 0.5，可按需调整）
3. 调整关灯延迟时间
4. 监控 CPU/内存使用，确保 24h 稳定运行

---

### 六、技术选型总结

| 组件 | 选型 | 理由 |
|------|------|------|
| 视频流接入 | go2rtc (Frigate内置) | 原生支持小米 P2P 协议，免刷固件 |
| NVR + 人体检测 | Frigate 0.17+ | HA 生态最成熟的方案，原生支持 Zone |
| AI 推理 | Apple Silicon Detector + YOLOv9-tiny | 充分利用 M4 Neural Engine，~10ms 推理 |
| 容器运行时 | OrbStack | Apple Silicon 优化，比 Docker Desktop 省资源 |
| 智能家居平台 | Home Assistant (UTM) | 你已有的部署，生态丰富 |
| 设备集成 | ha_xiaomi_home (官方) + hass-xiaomi-miot (兜底) | 官方集成活跃维护，兼容性更好 |
| 消息通信 | MQTT (Mosquitto) | Frigate 与 HA 之间的标准通信方式 |

---

### 七、扩展能力

这套架构在基础功能跑通后，可以轻松扩展以下场景：

**场景联动增强：** 结合光照传感器实现「天暗才开灯」；结合时间段实现「夜间低亮度模式」；结合人体存在时长实现「久坐提醒」。

**多设备联动：** Zone 事件不只可以控制灯，还可以联动空调、窗帘、音箱等任何接入 HA 的设备。例如，人进入卧室 → 开灯 + 关窗帘 + 空调调到睡眠模式。

**录像与回看：** Frigate 本身就是 NVR，配置存储路径后可以自动录制事件片段，配合 HA 的 Frigate 面板可以方便地回看。

**人脸识别（进阶）：** 如果需要区分家庭成员实现个性化灯光，可以在 Frigate 基础上叠加 Double Take + CompreFace，实现「爸爸进书房开白光，妈妈进书房开暖光」。

**外出安防：** 家中无人时自动切换为安防模式，检测到人体发送通知到手机。

---

### 八、资源消耗评估

| 资源 | 预估消耗 | 说明 |
|------|---------|------|
| CPU (M4) | ~15-25% | Frigate 解码 + go2rtc 转发（3-4 路摄像头）|
| Neural Engine | ~5-10% | YOLOv9 推理，每路 1FPS 非常轻松 |
| 内存 | ~2-3GB | Frigate + go2rtc 容器 |
| 网络带宽 | ~15-30Mbps | 3-4 路 1080p 局域网流 |
| 磁盘 | ~50MB/天 | 仅保存事件快照；如开录像则 ~5-10GB/天 |

M4 16GB 的配置跑这套系统绑绑有余，同时跑 UTM 虚拟机里的 HA 也不会有压力。

---

### 九、潜在风险与应对

**风险一：小米摄像头 P2P 协议变更。** 小米可能更新固件导致 go2rtc 的 xiaomi:// 协议失效。应对：go2rtc 社区活跃，通常会快速跟进适配；也可以暂时锁定摄像头固件版本。

**风险二：OrbStack Docker 与 UTM 虚拟机网络互通。** 这是本方案最需要注意的问题。OrbStack 的容器使用私有虚拟网络（198.19.x.x），而 UTM 桥接模式的虚拟机在物理局域网（192.168.x.x）上，两者属于不同网段。好消息是，OrbStack 容器的出站流量会通过 Mac 宿主机的网络栈进行 NAT，因此 Frigate 容器主动连接 HA 的 MQTT（出站方向）通常是可以工作的。应对方案分三级：方案 A（推荐先试）——部署后直接测试 Frigate 容器能否 ping 通 UTM 虚拟机的局域网 IP，大概率可通；方案 B——如不通，将 HA 也迁移到 OrbStack 中运行（OrbStack 支持运行 Linux 虚拟机，可以跑 HAOS），这样所有组件在同一网络栈；方案 C——改用 Docker Desktop 替代 OrbStack，其网络模型对局域网访问的支持更明确。

**风险三：检测误报/漏报。** 摄像头视角、光线变化可能影响检测准确率。应对：合理设置 Zone 边界留出缓冲区；调整检测置信度阈值；利用 Frigate 的 mask 功能屏蔽窗户等容易产生误报的区域。

**风险四：灯具频繁开关。** 人在 Zone 边界活动可能导致灯反复开关。应对：在 HA 自动化中加入 `for` 延迟条件（如离开 Zone 3 分钟后才关灯）；Frigate 也可以配置 `inactivity_timeout` 来平滑检测结果。
