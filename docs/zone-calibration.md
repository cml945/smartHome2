# Zone 标定说明

Frigate 的 Zone（区域）用于定义摄像头画面中的特定区域，当检测到人体进入/离开某个 Zone 时，会生成对应事件供 Home Assistant 自动化使用。

## 概念

- **Zone 坐标**：使用归一化值 (0.0~1.0)，左上角为 (0,0)，右下角为 (1,1)
- **多边形**：每个 Zone 由一组顶点坐标定义，至少 3 个顶点
- **坐标格式**：`x1,y1,x2,y2,x3,y3,...`（顺时针或逆时针排列）

## 标定步骤

### 1. 确保 Frigate 已启动

```bash
make up
```

### 2. 打开 Frigate Web UI

访问 http://localhost:8971

### 3. 进入摄像头配置

1. 点击左侧菜单中的摄像头名称
2. 进入 Zone 编辑界面

### 4. 绘制 Zone

1. 在画面中拖拽绘制多边形区域
2. 为每个 Zone 命名（如 `sofa_area`、`dining_area`）
3. 调整顶点位置使区域精确覆盖目标位置

### 5. 记录坐标

Frigate UI 会显示每个 Zone 的归一化坐标，将其复制到 `frigate/config.example.yml` 对应位置。

### 6. 重新生成配置

```bash
make config
make restart
```

## 标定建议

### Zone 边界留缓冲区

Zone 边界不要过于紧凑。建议在目标区域外围留出 5-10% 的缓冲区，避免人在边界位置导致频繁进出切换。

```
不推荐（太紧）：Zone 边界紧贴沙发边缘
推荐（有缓冲）：Zone 边界比沙发大一圈
```

### Zone 之间避免重叠

如果两个 Zone 有重叠区域，人在重叠处会同时触发两个区域的事件，导致两盏灯同时开启。根据实际需要决定是否允许重叠。

### 考虑摄像头视角

- 近处的区域精度更高，适合划分较小的 Zone
- 远处的区域由于透视会导致检测精度下降，Zone 不宜太小
- 摄像头安装位置建议在房间角落高处（2m+），俯视角度最佳

### 测试验证

标定完成后，在 Frigate Web UI 中观察：
1. 人走进 Zone 时，Zone 状态变为 "occupied"
2. 人走出 Zone 时，Zone 状态变为 "vacant"
3. 确认 HA 中对应的 `binary_sensor` 状态同步变化

## 示例 Zone 布局

### 客厅（典型配置）

```
┌──────────────────────────────────┐
│           entrance               │
│          ┌────────┐              │
│          │        │              │
│          └────────┘              │
│                                  │
│  ┌──────────┐  ┌──────────────┐ │
│  │          │  │              │ │
│  │  sofa    │  │   dining     │ │
│  │  area    │  │   area       │ │
│  │          │  │              │ │
│  └──────────┘  └──────────────┘ │
└──────────────────────────────────┘
```

### 卧室（典型配置）

```
┌──────────────────────────────────┐
│  ┌──────────────┐ ┌───────────┐ │
│  │              │ │           │ │
│  │   bed_area   │ │ desk_area │ │
│  │              │ │           │ │
│  │              │ │           │ │
│  └──────────────┘ └───────────┘ │
│                                  │
└──────────────────────────────────┘
```

## HA 中的 Zone 实体命名

Frigate Integration 会自动在 HA 中创建 binary_sensor 实体：

```
binary_sensor.<摄像头名>_<zone名>_person_occupancy
```

示例：
- `binary_sensor.living_room_sofa_area_person_occupancy`
- `binary_sensor.living_room_dining_area_person_occupancy`
- `binary_sensor.bedroom_bed_area_person_occupancy`
- `binary_sensor.bedroom_desk_area_person_occupancy`
- `binary_sensor.study_desk_area_person_occupancy`

这些 entity_id 直接用在 HA 自动化的 trigger 中。
