# 配置操作指南

本文档说明如何新增摄像头、检测区域和自动化规则。

## 系统架构概览

```
小米摄像头 --xiaomi://--> go2rtc (macOS原生) --RTSP--> Frigate (Docker)
                                                         |
                                                    MQTT 事件
                                                         |
                                    Mosquitto (Docker) <--+
                                         |
                                    Home Assistant (UTM VM)
                                         |
                                    小米智能灯具
```

| 组件 | 运行方式 | 配置文件 |
|------|---------|---------|
| go2rtc 1.9.14 | macOS 原生 (launchd) | `go2rtc/config.yml` |
| Frigate 0.15.0 | Docker (OrbStack) | `frigate/config/config.yml` |
| Mosquitto 2 | Docker (OrbStack) | `mosquitto/config/mosquitto.conf` |

---

## 一、新增摄像头

### 1. 获取摄像头参数

打开 go2rtc WebUI：http://localhost:1984

1. 点击页面底部 **Add** 按钮
2. 选择 **Xiaomi**，用小米账号登录
3. 选择目标摄像头，页面会显示 `xiaomi://` URL，格式如下：
   ```
   xiaomi://USER_ID:cn@CAMERA_IP?did=DEVICE_ID&model=MODEL
   ```
4. 复制这个 URL

### 2. 配置 go2rtc 流

编辑 `go2rtc/config.yml`，在 `streams` 下新增一条：

```yaml
streams:
  study_cam:
    - xiaomi://1280623889:cn@192.168.31.9?did=268058994&model=chuangmi.camera.ipc021
  # 新增摄像头
  living_room_cam:
    - xiaomi://USER_ID:cn@CAMERA_IP?did=DEVICE_ID&model=MODEL
```

重启 go2rtc：

```bash
launchctl unload ~/Library/LaunchAgents/com.go2rtc.plist
launchctl load ~/Library/LaunchAgents/com.go2rtc.plist
```

在 go2rtc WebUI 中验证新流是否能正常播放。

### 3. 配置 Frigate 摄像头

编辑 `frigate/config/config.yml`，在 `cameras` 下新增：

```yaml
cameras:
  study:
    # ... 已有配置 ...

  living_room:
    ffmpeg:
      inputs:
        - path: rtsp://host.docker.internal:8554/living_room_cam
          roles: [detect]
    detect:
      width: 1280
      height: 720
      fps: 1
    zones:
      sofa_area:
        coordinates: 0.1,0.5,0.6,0.5,0.6,0.95,0.1,0.95
        objects: person
    objects:
      track:
        - person
      filters:
        person:
          min_score: 0.5
          min_area: 5000
    snapshots:
      enabled: true
      bounding_box: true
    record:
      enabled: false
```

**注意事项**：
- `path` 中的流名称必须与 `go2rtc/config.yml` 中的 `streams` key 一致
- `width/height` 不需要与摄像头原始分辨率一致，Frigate 会自动缩放
- `fps: 1` 表示每秒检测一次，足够做人体感知

重启 Frigate：

```bash
docker restart frigate
```

### 4. 更新 HA 集成

Frigate 重启后，HA 的 Frigate 集成会自动发现新摄像头实体。如果没有自动出现：

1. 进入 HA → 设置 → 设备与服务 → Frigate
2. 点击三个点 → 重新加载

新摄像头会自动生成以下实体（以 `living_room` 为例）：
- `binary_sensor.living_room_person_occupancy`
- `binary_sensor.living_room_sofa_area_person_occupancy`
- `sensor.living_room_person_count`
- `camera.living_room`

---

## 二、新增检测区域 (Zone)

### Zone 坐标原理

Frigate 使用归一化坐标 (0.0~1.0) 定义多边形区域：
- **(0, 0)** = 画面左上角
- **(1, 1)** = 画面右下角

**关键规则**：Zone 要画在**人脚落点所在的地面区域**，而不是目标物体本身。因为 Frigate 根据人体检测框的**底部中心点**判断人在哪个 Zone。

```
     (0,0)──────────────(1,0)
       │                  │
       │   不要画在这里     │  ← 书柜、沙发等物体
       │                  │
       │  ┌────────────┐  │
       │  │ 画在这里     │  │  ← 人站在物体前面时脚的落点
       │  └────────────┘  │
     (0,1)──────────────(1,1)
```

### 方法 A：通过 Frigate WebUI 绘制（推荐）

1. 打开 https://192.168.31.233:8971
2. 登录后点击左下角 **Settings**（齿轮）
3. 选择 **Mask / Zone editor** 标签
4. 选择目标摄像头
5. 点击 **+ Add zone**
6. 在画面上点击多个点绘制多边形（框选**地面区域**）
7. 输入 Zone 名称（仅英文小写+下划线，如 `sofa_area`）
8. 点击 **Save**

### 方法 B：手动编辑配置文件

编辑 `frigate/config/config.yml`，在对应摄像头的 `zones` 下新增：

```yaml
    zones:
      desk_area:
        coordinates: 0.476,0.589,0.727,0.714,0.668,1,0.224,0.986,0.195,0.929
        objects: person
      # 新增区域
      door_area:
        coordinates: 0.45,0.4,0.65,0.4,0.65,0.75,0.45,0.75
        objects: person
```

然后重启 Frigate：`docker restart frigate`

### Zone 生效后的 HA 实体

每个 Zone 会自动生成以下 HA 实体：
- `binary_sensor.<zone名>_person_occupancy` — 是否有人（on/off）
- `sensor.<zone名>_person_count` — 人数
- `sensor.<zone名>_person_active_count` — 活跃人数

---

## 三、新增自动化规则

### 方法 A：通过 HA API 创建（推荐，可脚本化）

```bash
HA_TOKEN="你的长期访问令牌"

curl -X POST "http://192.168.31.224:8123/api/config/automation/config/自动化ID" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "alias": "客厅沙发区 - 有人开灯",
    "trigger": [{
      "platform": "state",
      "entity_id": "binary_sensor.living_room_sofa_area_person_occupancy",
      "to": "on"
    }],
    "action": [{
      "service": "light.turn_on",
      "target": {"entity_id": "light.你的灯具entity_id"},
      "data": {"brightness_pct": 80}
    }],
    "mode": "single"
  }'
```

### 方法 B：通过 HA 界面创建

1. 进入 HA → 设置 → 自动化与场景 → 创建自动化
2. 触发器：选择 **状态** → 实体选择 `binary_sensor.xxx_person_occupancy` → 变为 `on`
3. 动作：选择 **调用服务** → `light.turn_on` 或 `switch.turn_on` → 选择灯具
4. 保存

### 自动化模板

每个区域通常需要**一对**自动化（开灯 + 关灯）：

**开灯规则**：

| 配置项 | 说明 |
|--------|------|
| 触发器 | `binary_sensor.<zone>_person_occupancy` → `on` |
| 动作 | `light.turn_on` / `switch.turn_on` |

**关灯规则**：

| 配置项 | 说明 |
|--------|------|
| 触发器 | `binary_sensor.<zone>_person_occupancy` → `off`，持续 3~5 分钟 |
| 动作 | `light.turn_off` / `switch.turn_off` |

关灯延迟建议：
- 走廊/入口：2 分钟
- 书桌/客厅：5 分钟
- 卧室：5~10 分钟

### 灯具实体类型

- `light.*` 实体 → 使用 `light.turn_on` / `light.turn_off`（支持亮度、色温）
- `switch.*` 实体 → 使用 `switch.turn_on` / `switch.turn_off`（仅开关）
- `button.*` 实体 → 使用 `button.press`（触发式，无状态）

### 查找灯具 entity_id

在 HA 中进入 **开发者工具** → **状态**，筛选 `light.` 或 `switch.` 域，找到目标灯具。

---

## 四、当前配置清单

### 摄像头

| 名称 | 位置 | go2rtc 流名 | Frigate 摄像头名 |
|------|------|------------|----------------|
| 小米智能摄像机云台版Pro | 书房 | `study_cam` | `study` |

### Zone 区域

| Zone 名称 | 所属摄像头 | 覆盖区域 |
|-----------|-----------|---------|
| `desk_area` | study | 书桌前方地面 |
| `book_shelf` | study | 书柜前方地面 |

### 自动化规则

| 自动化名称 | 触发实体 | 动作 |
|-----------|---------|------|
| 书房桌面区 - 有人开挂灯 | `desk_area_person_occupancy → on` | 开显示器挂灯 |
| 书房桌面区 - 无人关挂灯 | `desk_area_person_occupancy → off 5min` | 关显示器挂灯 |
| 书房 - 有人开大灯 | `study_person_occupancy → on` | 开吸顶灯 |
| 书房 - 无人关大灯 | `study_person_occupancy → off 5min` | 关吸顶灯 |
| 书柜区域 - 有人开灯带 | `book_shelf_person_occupancy → on` | 开书柜灯带 |
| 书柜区域 - 无人关灯带 | `book_shelf_person_occupancy → off 3min` | 关书柜灯带 |

---

## 五、常用命令

```bash
# go2rtc
launchctl list | grep go2rtc          # 查看 go2rtc 服务状态
launchctl unload ~/Library/LaunchAgents/com.go2rtc.plist  # 停止
launchctl load ~/Library/LaunchAgents/com.go2rtc.plist    # 启动

# Docker 服务
cd docker && docker compose up -d     # 启动所有服务
docker restart frigate                # 重启 Frigate
docker logs frigate --tail 30         # 查看 Frigate 日志
docker logs mosquitto --tail 30       # 查看 MQTT 日志

# 调试
curl -s http://localhost:1984/api/streams                  # go2rtc 流列表
curl -s http://localhost:1984/api/frame.jpeg?src=study_cam -o frame.jpg  # 抓一帧
docker exec mosquitto mosquitto_sub -t "frigate/#" -v -C 5 # 查看 MQTT 消息

# Frigate WebUI
# https://192.168.31.233:8971 (admin / 查看 docker logs frigate 中的 Password)
```
