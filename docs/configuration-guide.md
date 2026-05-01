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
| Home Assistant | UTM 虚拟机 (192.168.31.224) | HA 内部配置 |

---

## 一、新增摄像头（完整流程）

完整接入一个新摄像头需要依次配置 4 层：go2rtc → Frigate → HA 集成 → HA 自动化。

### 1. 获取摄像头参数

打开 go2rtc WebUI：http://localhost:1984

1. 点击页面底部 **Add** 按钮
2. 选择 **Xiaomi**（已有 token 会自动列出账号下所有设备）
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
  # 新增摄像头：流名称将作为 Frigate RTSP 路径的一部分
  living_room_cam:
    - xiaomi://USER_ID:cn@CAMERA_IP?did=DEVICE_ID&model=MODEL
```

重启 go2rtc：

```bash
launchctl unload ~/Library/LaunchAgents/com.go2rtc.plist
launchctl load ~/Library/LaunchAgents/com.go2rtc.plist
```

**验证**：在 go2rtc WebUI（http://localhost:1984）中确认新流出现且可播放。

### 3. 配置 Frigate 摄像头

编辑 `frigate/config/config.yml`，在 `cameras` 下新增：

```yaml
cameras:
  # 新增摄像头名称（英文小写+下划线，此名称会出现在 HA 实体中）
  living_room:
    ffmpeg:
      inputs:
        - path: rtsp://host.docker.internal:8554/living_room_cam
          roles: [detect]
    detect:
      width: 1280
      height: 720
      fps: 5    # M4 芯片推理仅 7ms，可设 5fps
    zones:
      # Zone 坐标先用占位值，后续通过 WebUI 绘制精确坐标
      sofa_area:
        coordinates: 0.1,0.5,0.6,0.5,0.6,0.95,0.1,0.95
        inertia: 3
        loitering_time: 0
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

重启 Frigate：

```bash
docker restart frigate
```

**验证**：打开 Frigate WebUI（https://192.168.31.233:8971），确认新摄像头出现且有画面。

### 4. 重新加载 HA Frigate 集成

> **重要**：Frigate 重启后，HA **不会自动发现**新摄像头实体，必须手动重新加载集成。

**方法 A：通过 API 重新加载（推荐）**

```bash
# 1. 查找 Frigate 集成的 entry_id
source .env
ENTRY_ID=$(curl -s "http://${HA_IP}:8123/api/config/config_entries/entry" \
  -H "Authorization: Bearer $HA_TOKEN" | \
  python3 -c "import json,sys; [print(e['entry_id']) for e in json.load(sys.stdin) if e['domain']=='frigate']")

# 2. 重新加载（注意：会导致所有 Frigate 传感器短暂重置，见下方警告）
curl -s -X POST "http://${HA_IP}:8123/api/config/config_entries/entry/${ENTRY_ID}/reload" \
  -H "Authorization: Bearer $HA_TOKEN"
```

**方法 B：通过 HA 界面**

1. 进入 HA → 设置 → 设备与服务 → Frigate
2. 点击三个点 → 重新加载

> **警告：重新加载 Frigate 集成会短暂重置所有传感器**
>
> 重新加载时，所有 Frigate 实体会瞬间经历 `on → unavailable → off` 的状态变化。
> 如果已有"无人 X 分钟后关灯"的自动化，这个 `off` 状态会**启动关灯倒计时**，
> 可能导致人在房间内灯却被关掉。
>
> **安全做法**：重新加载前，先暂时禁用现有的关灯自动化，加载完成后再启用。

### 5. 验证 HA 实体

等待几秒后，确认新实体已出现：

```bash
source .env
curl -s "http://${HA_IP}:8123/api/states" \
  -H "Authorization: Bearer $HA_TOKEN" | \
  python3 -c "
import json,sys
for e in json.load(sys.stdin):
    if 'living_room' in e['entity_id']:
        print(f\"{e['entity_id']}: {e['state']}\")
"
```

新摄像头会生成以下实体（以 `living_room` 为例）：
- `camera.living_room` — 摄像头实体
- `binary_sensor.living_room_person_occupancy` — 整个画面是否有人
- `binary_sensor.living_room_motion` — 是否有运动
- `switch.living_room_detect` — 检测开关

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
        inertia: 3
        loitering_time: 0
        objects: person
      # 新增区域
      door_area:
        coordinates: 0.45,0.4,0.65,0.4,0.65,0.75,0.45,0.75
        inertia: 3
        loitering_time: 0
        objects: person
```

然后重启 Frigate：`docker restart frigate`

### Zone 实体命名规则

> **重要**：Zone 对应的 HA 实体**不带摄像头名称前缀**，仅用 zone 名称。

| Frigate 摄像头 | Zone 名称 | HA 实体 |
|---------------|-----------|---------|
| `living_room` | `coffee_area` | `binary_sensor.coffee_area_person_occupancy` |
| `living_room` | `sofa_area` | `binary_sensor.sofa_area_person_occupancy` |
| `study` | `desk_area` | `binary_sensor.desk_area_person_occupancy` |

每个 Zone 会自动生成以下 HA 实体：
- `binary_sensor.<zone名>_person_occupancy` — 是否有人（on/off）
- `sensor.<zone名>_person_count` — 人数
- `sensor.<zone名>_person_active_count` — 活跃人数

### Zone 验证方法

绘制 Zone 后，需要走到区域内验证是否生效：

```bash
# 1. 监听 MQTT 事件，确认 current_zones 是否包含你的 zone 名称
docker exec mosquitto mosquitto_sub -t "frigate/events" -v -C 3 -W 15

# 2. 在输出的 JSON 中检查：
#    "current_zones": ["coffee_area"]   ← 正确，人在 zone 中
#    "current_zones": []                ← 错误，人不在任何 zone 中
```

如果 Frigate 能检测到人但 `current_zones` 为空，说明 zone 坐标范围不够大，
需要在 WebUI 中重新调整 zone 边界，确保覆盖人脚的落点位置。

---

## 三、新增自动化规则

### 方法 A：通过 HA API 创建（推荐，可脚本化）

```bash
source .env

# 开灯自动化
curl -X POST "http://${HA_IP}:8123/api/config/automation/config/my_zone_light_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "alias": "客厅咖啡区 - 有人开灯",
    "trigger": [{
      "platform": "state",
      "entity_id": "binary_sensor.coffee_area_person_occupancy",
      "to": "on"
    }],
    "condition": [],
    "action": [{
      "service": "switch.turn_on",
      "target": {"entity_id": "switch.your_light_entity_id"}
    }],
    "mode": "single"
  }'

# 关灯自动化
curl -X POST "http://${HA_IP}:8123/api/config/automation/config/my_zone_light_off" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "alias": "客厅咖啡区 - 无人关灯",
    "trigger": [{
      "platform": "state",
      "entity_id": "binary_sensor.coffee_area_person_occupancy",
      "to": "off",
      "for": {"minutes": 1}
    }],
    "condition": [],
    "action": [{
      "service": "switch.turn_off",
      "target": {"entity_id": "switch.your_light_entity_id"}
    }],
    "mode": "single"
  }'
```

> **注意**：`condition` 字段设为空数组 `[]` 表示无条件触发。
> 如果需要条件控制（如总开关、勿扰模式），必须先确保对应的
> `input_boolean` 辅助实体已在 HA 中创建，否则条件检查会静默失败，
> 导致自动化永远不触发。

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
| 触发器 | `binary_sensor.<zone>_person_occupancy` → `off`，持续 N 分钟 |
| 动作 | `light.turn_off` / `switch.turn_off` |

关灯延迟建议：
- 走廊/入口/咖啡区：1~2 分钟
- 书桌/客厅：3~5 分钟
- 卧室：5~10 分钟

### 灯具实体类型

- `light.*` 实体 → 使用 `light.turn_on` / `light.turn_off`（支持亮度、色温）
- `switch.*` 实体 → 使用 `switch.turn_on` / `switch.turn_off`（仅开关）
- `button.*` 实体 → 使用 `button.press`（触发式，无状态）

### 查找灯具 entity_id

在 HA 中进入 **开发者工具** → **状态**，筛选 `light.` 或 `switch.` 域，找到目标灯具。

### 自动化验证清单

创建自动化后，按以下步骤验证：

1. **确认触发实体存在** — 在 HA 开发者工具 → 状态 中搜索 `binary_sensor.<zone>_person_occupancy`，确保不是 "未知实体"
2. **确认自动化已启用** — 在 HA 自动化列表中确认开关为 on
3. **触发测试** — 走进 zone 区域，观察传感器状态是否变为 `on`
4. **检查 last_triggered** — 在自动化详情中查看 `last_triggered` 是否更新
5. **如果不触发** — 检查 condition 中引用的所有实体是否存在且状态正确

---

## 四、常见问题排查

### 问题 1：Frigate 能检测到人，但 Zone 传感器不变化

**原因**：人体检测框的底部中心点不在 Zone 多边形内。

**排查方法**：
```bash
# 监听 MQTT 事件，查看 current_zones 字段
docker exec mosquitto mosquitto_sub -t "frigate/events" -v -C 3 -W 15
# 如果 current_zones 为空，说明 zone 坐标需要扩大
```

**解决**：在 Frigate WebUI 的 Zone editor 中重新绘制，确保 zone 覆盖人实际站立的地面位置。

### 问题 2：HA 中看不到新摄像头的实体

**原因**：添加新摄像头后未重新加载 HA 的 Frigate 集成。

**解决**：
```bash
source .env
# 查找 entry_id
ENTRY_ID=$(curl -s "http://${HA_IP}:8123/api/config/config_entries/entry" \
  -H "Authorization: Bearer $HA_TOKEN" | \
  python3 -c "import json,sys; [print(e['entry_id']) for e in json.load(sys.stdin) if e['domain']=='frigate']")
# 重新加载
curl -s -X POST "http://${HA_IP}:8123/api/config/config_entries/entry/${ENTRY_ID}/reload" \
  -H "Authorization: Bearer $HA_TOKEN"
```

> **警告**：重新加载前先禁用现有的关灯自动化，避免传感器重置误触发关灯。

### 问题 3：自动化存在但永远不触发（last_triggered 为 None）

**可能原因**：

| 原因 | 排查方法 |
|------|---------|
| 触发实体名称错误 | HA 开发者工具搜索实体，确认是否显示为 "未知实体" |
| Zone 传感器命名写错 | 实体名是 `<zone>_person_occupancy`，**不带摄像头名称前缀** |
| condition 引用了不存在的实体 | 检查 `input_boolean.*` 等辅助实体是否在 HA 中已创建 |
| 传感器一直是 off | Zone 坐标问题，参考问题 1 |

### 问题 4：go2rtc 摄像头 401 Unauthorized / Token 过期

**症状**：Frigate 所有摄像头显示 "No frames have been received, check error logs"，
go2rtc 日志持续报 `streams: 401 Unauthorized`。

**根因**：小米 `passToken` 有效期约 72 小时，过期后 go2rtc 无法从小米云端获取摄像头的
P2P 连接凭据。go2rtc v1.9.14 没有自动刷新 token 的机制。
此外，v1.9.14 的 WebUI 登录存在已知 bug（[#2237](https://github.com/AlexxIT/go2rtc/issues/2237)），
即使重新登录也可能拿到无效的 token。

**排查步骤**：

```bash
# 1. 确认 go2rtc 进程是否存活
pgrep -fl go2rtc
launchctl list | grep go2rtc    # 退出码应为 0

# 2. 查看 go2rtc 日志，确认是否为 401 错误
curl -s http://127.0.0.1:1984/api/log | tail -10
# 如果看到 "streams: 401 Unauthorized" → token 过期，继续下面的修复步骤
# 如果看到 "read udp: i/o timeout" → 认证正常但 P2P 连接超时，检查摄像头网络
```

**修复方案：使用 get_xiaomi_token.py 脚本刷新 token**

> 由于 go2rtc WebUI 的小米登录流程存在 bug，推荐使用项目自带的
> `scripts/get_xiaomi_token.py` 脚本获取 token（该脚本完整实现了
> 小米的账号密码登录 + 短信验证码的认证流程）。

```bash
# Step 1: 运行脚本（需要手机号和密码，并接收短信验证码）
python3 scripts/get_xiaomi_token.py

# 脚本流程：
#   1. 输入小米账号（手机号，不是数字用户ID）和密码
#   2. 小米发送短信验证码到你的手机
#   3. 输入验证码，脚本获取 passToken
#   4. 选择 y 自动写入 go2rtc/config.yml

# Step 2: 重启 go2rtc
launchctl stop com.go2rtc && launchctl start com.go2rtc

# Step 3: 验证修复
sleep 5
curl -s http://127.0.0.1:1984/api/log | tail -5
# 不再出现 "401 Unauthorized" 即表示修复成功
# 如果出现 "read udp: i/o timeout" 则是摄像头网络问题，非 token 问题
```

**脚本依赖**：首次使用需安装 `requests` 库：

```bash
pip3 install requests
```

**注意事项**：
- 登录时必须使用**手机号**（如 `17342016281`），不能用数字用户 ID（如 `1280623889`）
- 小米可能要求短信验证码（flag=4）或邮箱验证码（flag=8），按提示操作即可
- Token 约 3 天过期一次，目前需要手动重新执行脚本刷新
- 相关上游 issue：[#2233](https://github.com/AlexxIT/go2rtc/issues/2233)（token 自动刷新）、
  [#2237](https://github.com/AlexxIT/go2rtc/issues/2237)（WebUI 登录 bug）

### 问题 5：重新加载 Frigate 集成后，灯被意外关闭

**原因**：重新加载导致所有 Frigate 传感器状态经历 `on → unavailable → off`，
触发了"无人 N 分钟关灯"的倒计时。

**预防**：在重新加载 Frigate 集成前后执行：
```bash
source .env
# 加载前：禁用所有关灯自动化
for id in presence_study_ceiling_light_off presence_study_desk_monitor_light_off pkg_presence_living_room_coffee_off; do
  curl -s -X POST "http://${HA_IP}:8123/api/services/automation/turn_off" \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"entity_id\": \"automation.$(echo $id | tr '_' '_')\"}" > /dev/null
done

# ... 执行重新加载 ...

# 加载后等待 30 秒让传感器恢复，再启用
sleep 30
for id in presence_study_ceiling_light_off presence_study_desk_monitor_light_off pkg_presence_living_room_coffee_off; do
  curl -s -X POST "http://${HA_IP}:8123/api/services/automation/turn_on" \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"entity_id\": \"automation.$(echo $id | tr '_' '_')\"}" > /dev/null
done
```

---

## 五、当前配置清单

### 摄像头

| 名称 | 位置 | go2rtc 流名 | Frigate 摄像头名 |
|------|------|------------|----------------|
| 小米智能摄像机云台版Pro | 书房 | `study_cam` | `study` |
| 小米智能摄像机 039c01 | 客厅 | `living_room_cam` | `living_room` |

### Zone 区域

| Zone 名称 | 所属摄像头 | HA 实体 | 覆盖区域 |
|-----------|-----------|---------|---------|
| `desk_area` | study | `binary_sensor.desk_area_person_occupancy` | 书桌前方地面 |
| `book_shelf` | study | `binary_sensor.book_shelf_person_occupancy` | 书柜前方地面 |
| `coffee_area` | living_room | `binary_sensor.coffee_area_person_occupancy` | 客厅咖啡区 |

### 自动化规则

| 自动化名称 | 触发实体 | 动作 |
|-----------|---------|------|
| 书房桌面区 - 有人开挂灯 | `desk_area_person_occupancy → on` | 开显示器挂灯 |
| 书房桌面区 - 无人关挂灯 | `desk_area_person_occupancy → off 5min` | 关显示器挂灯 |
| 书房 - 有人开大灯 | `study_person_occupancy → on` | 开吸顶灯 |
| 书房 - 无人关大灯 | `study_person_occupancy → off 5min` | 关吸顶灯 |
| 书柜区域 - 有人开灯带 | `book_shelf_person_occupancy → on` | 开书柜灯带 |
| 书柜区域 - 无人关灯带 | `book_shelf_person_occupancy → off 3min` | 关书柜灯带 |
| 客厅咖啡区 - 有人开灯 | `coffee_area_person_occupancy → on` | 开灯 (switch) |
| 客厅咖啡区 - 无人关灯 | `coffee_area_person_occupancy → off 1min` | 关灯 (switch) |

---

## 六、常用命令

```bash
# ====== go2rtc ======
launchctl list | grep go2rtc          # 查看 go2rtc 服务状态
launchctl stop com.go2rtc             # 停止
launchctl start com.go2rtc            # 启动
curl -s http://localhost:1984/api/log | tail -10  # 查看日志（检查 401 等错误）
python3 scripts/get_xiaomi_token.py   # 刷新小米 token（token 约 3 天过期）

# ====== Docker 服务 ======
cd docker && docker compose up -d     # 启动所有服务
docker restart frigate                # 重启 Frigate
docker logs frigate --tail 30         # 查看 Frigate 日志
docker logs mosquitto --tail 30       # 查看 MQTT 日志

# ====== 调试 ======
# go2rtc 流状态
curl -s http://localhost:1984/api/streams
# 抓一帧画面
curl -s http://localhost:1984/api/frame.jpeg?src=study_cam -o frame.jpg
# 查看 Frigate MQTT 事件（含 zone 信息）
docker exec mosquitto mosquitto_sub -t "frigate/events" -v -C 5
# 查看 MQTT 所有 Frigate 消息
docker exec mosquitto mosquitto_sub -t "frigate/#" -v -C 10

# ====== HA API ======
source .env
# 查看特定传感器状态
curl -s "http://${HA_IP}:8123/api/states/binary_sensor.coffee_area_person_occupancy" \
  -H "Authorization: Bearer $HA_TOKEN" | python3 -m json.tool
# 查看传感器历史
curl -s "http://${HA_IP}:8123/api/history/period?filter_entity_id=binary_sensor.coffee_area_person_occupancy" \
  -H "Authorization: Bearer $HA_TOKEN" | python3 -m json.tool
# 手动开灯测试
curl -s -X POST "http://${HA_IP}:8123/api/services/switch/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "switch.your_entity_id"}'

# ====== Frigate WebUI ======
# https://192.168.31.233:8971 (admin / 查看 docker logs frigate 中的 Password)
```
