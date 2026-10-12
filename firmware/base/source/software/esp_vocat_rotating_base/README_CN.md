# ESP-VoCat-Base: 喵伴旋转底座

**中文** | [English](README.md)

## 项目简介

该项目工程是乐鑫"喵伴"智能交互设备的旋转底座固件，基于 ESP32-C61 芯片开发。该项目实现了步进电机精确控制、磁吸滑动开关检测、串口通信等功能，为智能设备提供生动的交互动作和灵敏的输入检测。

## 主要特性

### 步进电机控制
- **多种预定义动作**：
  - 摇头动作（固定幅度/渐变递减）
  - 左顾右盼观察动作
  - 猫咪蹭手动作
  - 跟随鼓点左右摆头动作

所有动作都可参数化调控，便于对接豆包、小智等大模型。
- **精确角度控制**：支持任意角度旋转，精度可达 0.1 度
- **平滑加减速**：采用加减速算法，运动更自然流畅
- **半步驱动模式**：提高精度 2 倍，运动更平滑
- **自动归位校准**：启动时通过限位开关自动回零

### 磁吸滑动开关
- **多种传感器支持**：
  - BMM150 三轴地磁传感器
  - QMC6309 三轴地磁传感器
  - 线性霍尔传感器

以上三种传感器三选一即可，默认选择 BMM150。
- **丰富的事件检测**：
  - 滑块上下滑动（SLIDE_UP / SLIDE_DOWN）
  - 从上/下位置取下（REMOVE_FROM_UP / REMOVE_FROM_DOWN）
  - 从上/下位置放回（PLACE_FROM_UP / PLACE_FROM_DOWN）
  - 单击事件（SINGLE_CLICK）：基于 mag_x 轴数据变化检测，结合 mag_x 和 mag_y 关系判断，防止滑动误触发
  - 喂鱼事件（FISH_ATTACHED）：检测小鱼从上位置放置的特定磁场变化
  - 配对模式（PAIRING）：检测长时间处于特定磁场状态
- **智能校准功能**：
  - 全自动校准流程：无需手动操作提示，系统自动检测三个稳定位置
  - 快速稳定检测：500ms 稳定时间（BMM150）/ 2000ms（QMC6309）
  - 智能位置识别：自动判断位置差异，避免重复记录
  - 校准数据持久化存储（NVS Flash）
  - 支持串口命令触发重新校准
  - 支持长按 Boot 键触发重新校准
  - 自动适配不同磁场环境

### 串口通信
- **UART 协议通信**：与 ESP-VoCat 进行数据通信
- **命令类型**：
  - 角度控制命令（CMD_BASE_ANGLE_CONTROL）
  - 预设动作控制命令（CMD_BASE_ACTION_CONTROL）
  - 预设动作完成通知（CMD_ACTION_COMPLETE）
  - 磁吸开关事件上报（CMD_MAGNETIC_SWITCH_EVENT）
  - 校准进度通知（MAG_SWITCH_CALIB_*）
- **数据帧格式**：`AA 55 [LEN_H] [LEN_L] [CMD] [DATA...] [CHECKSUM]`
- **校验机制**：支持**帧头**校验、**校验和**验证

## 硬件要求

### 主控芯片
- **ESP32-C61-WROOM-1** 或 **ESP32-C61-WROOM-1U** 模组

默认使用 ESP32-C61-WROOM-1

### 外设连接与引脚分配

#### 步进电机（28BYJ-48 + ULN2003 驱动 IC）
- IN1 → GPIO28
- IN2 → GPIO27
- IN3 → GPIO26
- IN4 → GPIO25

#### 磁力计传感器（I2C）
- SCL → GPIO3
- SDA → GPIO2
- 支持 BMM150（I2C 地址：0x10）
- 支持 QMC6309（I2C 地址：0x7C）

#### 线性霍尔传感器（ADC）
- ADC_PIN → GPIO5（ADC_CHANNEL_3）

#### 转台回零校准限位开关
- 回零校准限位开关检测引脚 → GPIO1（低电平有效）

#### BOOT 按键
- BOOT 按键 → GPIO9（低电平有效）

默认用于长按进行磁吸滑动开关校准。

#### UART 通信
- TXD → GPIO29
- RXD → GPIO8
- 波特率：115200

## 软件架构

### 主要组件

#### 1. stepper_motor（步进电机控制）
- 实现半步驱动控制算法
- 提供加减速曲线
- 封装多种预定义动作
- 支持精确角度旋转

#### 2. magnetic_slide_switch（磁吸滑动开关）
- 滑动窗口滤波算法（mag_z 轴）
- 状态机实现事件检测
- 单击检测（基于 mag_x 轴变化 + mag_x/mag_y 关系判断）
- 喂鱼事件检测（特定磁场增量范围）
- 配对模式检测（两个 ESP-VoCat 面对面靠近，用于配对联网）
- 全自动校准流程（时间基准 + 智能位置识别）
- 校准数据 NVS 持久化存储

#### 3. control_serial（串口通信）
- UART 收发任务
- 协议帧解析
- 命令分发处理
- 事件上报

#### 4. BMM150_SensorAPI（传感器驱动）
- BMM150 官方 API 适配
- I2C 总线封装

### 任务结构
```
app_main
├── base_calibration_task          // 底座角度校准任务（启动时，完成后销毁）
├── uart_cmd_receive_task          // UART 命令接收任务
├── magnetic_detect_task           // 磁吸附状态周期上报任务
├── magnetometer_calibration_task  // 磁吸开关自动校准任务
└── slide_switch_event_detect_task // 磁吸滑动开关事件检测任务
```

## 快速开始

### 环境准备

1. **ESP-IDF 版本**

- ESP-IDF release/v5.5(v5.5-445-g1191f4c4f91-dirty)

### 配置项目

使用 `idf.py menuconfig` 选择磁吸滑动开关所用的传感器：

 `Component config → Magnetic Slide Switch → Sensor Type`

选择传感器类型：BMM150 / QMC6309 / Linear Hall Sensor

### 编译烧录

```bash
# 编译项目
idf.py build
# 烧录固件
idf.py flash
# 查看日志
idf.py monitor
```

## 使用说明

### 首次启动

1. **底座角度校准**：
   - 上电后，电机会自动向左旋转寻找限位开关
   - 触碰到限位开关后，电机会向右旋转配置的中心偏移角（当前固件为 70°）到中心位置
   - 校准完成后，电机断电

2. **磁吸开关自动校准**（如果是首次使用）：
   - 系统自动检测第一个稳定位置（保持滑块静止约 500ms）
   - 系统记录第一个位置后，提示：`First position calibrated`
   - 将滑块移动到第二个不同位置（上/下/拿掉），系统自动检测稳定
   - 系统记录第二个位置后，发送 `0x12` 给 ESP-VoCat，通知第二个位置校准完成
   - 将滑块移动到第三个不同位置，系统自动检测稳定
   - 系统记录第三个位置后，自动按磁场强度排序分配到 REMOVED/UP/DOWN
   - 校准完成后发送 `0x13` 给 ESP-VoCat，通知校准完毕
   - 校准数据自动保存到 Flash，下次启动自动加载
   
   **注意**：
   - 每个位置需要保持静止，等待系统自动检测稳定（约 500ms）
   - 三个位置的磁场差异需大于 100（系统自动判断）
   - 移动过程中数据变化正常，系统会自动等待稳定后记录

### 重新校准

**方法 1：长按 Boot 按键**
- 长按 GPIO9 的 Boot 按键（约 1.5 秒）
- 系统自动进入重新校准模式

**方法 2：串口命令**
- 发送命令：`AA 55 00 03 03 00 10 13`
- 系统收到命令后进入重新校准模式

### 串口命令

#### 1. 磁吸通知命令（0x00）
`
AA 55 00 03 00 00 01 01
`

底座会周期性发送该命令，ESP-VoCat 在被磁吸连接后若成功接收到此命令，即可确认已与底座建立连接，并开始播放连接动画。

#### 2. 角度控制命令（0x01）
**格式**：`AA 55 00 03 01 [ANGLE_H] [ANGLE_L] [CHECKSUM]`

**示例**：旋转到 45°
```
AA 55 00 03 01 00 2D 2E
```
- `00 2D` = 45（十进制）
- `2E` = `01 + 00 + 2D`（校验和）

#### 3. 动作控制命令（0x02）
**格式**：`AA 55 00 03 02 00 [ACTION] [CHECKSUM]`

**动作码**：
- `0x01`：摇头（SHAKE_HEAD）
- `0x02`：左顾右盼（LOOK_AROUND）
- `0x03`：跟随鼓点-鼓类（BEAT_SWING_DRUM）
- `0x04`：猫咪蹭手（CAT_NUZZLE）
- `0x05`：跟随鼓点-其他乐器（BEAT_SWING_OTHER）

**示例**：执行左顾右盼动作
```
AA 55 00 03 02 00 02 04
```

#### 4. 磁吸开关事件上报（0x03）
**格式**：`AA 55 00 03 03 00 [EVENT] [CHECKSUM]`

**事件码**：
- `0x01`：滑块向下滑动（SLIDE_DOWN）
- `0x02`：滑块向上滑动（SLIDE_UP）
- `0x03`：从上面拿下（REMOVE_FROM_UP）
- `0x04`：从下面拿下（REMOVE_FROM_DOWN）
- `0x05`：从上面放回（PLACE_FROM_UP）
- `0x06`：从下面放回（PLACE_FROM_DOWN）
- `0x07`：单击事件（SINGLE_CLICK）
- `0x08`：喂鱼事件（FISH_ATTACHED）- 小鱼从上位置放置时触发
- `0x09`：配对模式（PAIRING）- 长时间处于特定磁场状态
- `0x10`：启动数据校准程序
- `0x11`：校准开始通知
- `0x12`：第二个位置校准完成（头部收到后语音提示第三步）
- `0x13`：校准完成

#### 5. 动作完成通知（0x02）
**格式**：`AA 55 00 03 02 00 10 12`

- 底座在执行完任意特定动作后自动发送
- 头部可根据此命令同步状态

## 步进电机动作详解

### 1. stepper_shake_head（摇头）
```c
stepper_shake_head(float amplitude, int cycles, int speed_us);
```
- `amplitude`：摆动幅度（度）
- `cycles`：摆动次数
- `speed_us`：速度（微秒/步）

### 2. stepper_shake_head_decay（渐变摇头）
```c
stepper_shake_head_decay(float initial_amplitude, float decay_rate, int speed_us);
```
- `initial_amplitude`：初始幅度（度）
- `decay_rate`：衰减系数（0.0-1.0，如 0.8 表示每次衰减 20%）
- `speed_us`：速度（微秒/步）

### 3. stepper_look_around（左顾右盼）
```c
stepper_look_around(float left_angle, float right_angle, float scan_angle, 
                    int pause_ms, int large_speed_us, int small_speed_us);
```
- `left_angle`：左侧最大角度
- `right_angle`：右侧最大角度
- `scan_angle`：小幅度扫视角度
- `pause_ms`：每次转动后的停顿时间
- `large_speed_us`：大幅度转动速度
- `small_speed_us`：小幅度扫视速度

### 4. stepper_cat_nuzzle（猫咪蹭手）
```c
stepper_cat_nuzzle(float angle, int cycles, int speed_us);
```
- `angle`：蹭动幅度（度）
- `cycles`：蹭动次数
- `speed_us`：速度（微秒/步）

### 5. stepper_beat_swing（跟随鼓点）
```c
stepper_beat_swing(float angle, int speed_us);
```
- `angle`：摆动角度（度）
- `speed_us`：速度（微秒/步）
- **智能摆动逻辑**：
  - 同类乐器连续触发 → 向另一侧摆动 15 度

## 磁吸开关校准原理

### 自动校准流程状态机

```
CALIBRATION_DETECTING_FIRST      // 自动检测第一个稳定位置
         ↓                       // 数据稳定 500ms 后自动记录
CALIBRATION_WAITING_CHANGE_1     // 等待数据变化（用户移动滑块）
         ↓                       // 检测到变化量 > 100
CALIBRATION_DETECTING_SECOND     // 自动检测第二个稳定位置
         ↓                       // 数据稳定 500ms 后自动记录
         |                       // （发送 0x12 通知头部）
CALIBRATION_WAITING_CHANGE_2     // 等待数据变化（用户移动滑块）
         ↓                       // 检测到变化量 > 100
CALIBRATION_DETECTING_THIRD      // 自动检测第三个稳定位置
         ↓                       // 数据稳定 500ms 后自动记录
CALIBRATION_COMPLETED            // 自动排序并保存到 Flash
                                 // （发送 0x13 通知头部）
```

### 关键技术点

1. **滑动窗口滤波**：5 点滑动平均，降低噪声干扰
2. **时间基准稳定性检测**：
   - 使用系统时钟（FreeRTOS Tick）精确计时
   - 数据变化小于 10 个单位视为稳定
   - BMM150：连续稳定 500ms 后记录
   - QMC6309：连续稳定 500ms 后记录
3. **智能位置识别**：
   - 自动检测数据变化量（阈值 100）
   - 新位置必须与已记录位置差异明显
   - 防止重复记录同一位置
4. **自动排序分配**：
   - 按磁场强度从小到大排序
   - 最小值 → REMOVED（拿掉）
   - 中间值 → UP（上位置）
   - 最大值 → DOWN（下位置）
5. **NVS 持久化**：校准数据保存到 Flash，掉电不丢失
6. **无需手动提示**：全程自动检测，用户只需按顺序移动滑块并保持静止

### 磁场值参考范围（BMM150）
- **REMOVED**（拿掉）：~700-800（默认 709）
- **UP**（上位置）：~1300-1400（默认 1383）
- **DOWN**（下位置）：~2000-2100（默认 2047）
- **FISH_ATTACHED**（喂鱼）：REMOVED + 150~200

*注：实际值因磁铁强度和安装位置而异，自动校准会根据实际磁场分配位置*

### 事件检测特点

**单击事件检测**：
- 基于 mag_x 轴数据变化（drop/rise threshold: 100）
- 结合 mag_x 和 mag_y 大小关系判断滑块位置
- 仅在滑块处于 DOWN 位置时检测（mag_x > mag_y）
- 持续时间过滤：< 300ms 为有效单击
- 防止快速滑动误触发

**喂鱼事件检测**：
- 仅在 REMOVED 位置后检测
- 磁场增量在 150-200 范围内
- 区别于 PLACE_FROM_UP（增量更大，接近 UP_CENTER）

**配对模式检测**：
- REMOVED 位置后磁场继续下降
- 下降量 > 100（BMM150）/ 150（QMC6309）
- 稳定保持该状态

### 常见问题

**Q1：电机抖动或不转**
- 检查供电是否充足（至少 5V 1A）
- 检查接线是否正确
- 尝试降低速度（增大 `speed_us` 参数）

**Q2：磁吸开关校准失败**
- 确保传感器 I2C 连接正常
- 检查传感器类型配置是否匹配
- 每个位置需保持静止至少 500ms（BMM150）/ 2000ms（QMC6309）
- 三个位置的磁场差异需大于 100，确保位置明确不同
- 如移动过快导致检测不到变化，可稍等片刻让系统重新检测

**Q3：单击事件误触发或不触发**
- 误触发：可能是快速滑动导致，系统已通过 mag_x/mag_y 关系优化
- 不触发：确保滑块在 DOWN 位置（mag_x > mag_y），单击动作要明确快速
- 单击时长应小于 300ms，过慢会被识别为滑动

**Q4：串口通信异常**
- 检查波特率是否为 115200
- 确认 TX/RX 接线是否正确
- 检查校验和是否计算正确

**Q5：首次上电电机旋转方向错误**
- 检查限位开关安装位置
- 调整 `base_calibration_task` 中的旋转角度

## 性能参数

- **角度控制精度**：±0.5°
- **最快旋转速度**：600 μs/步（约 200°/秒）
- **磁吸开关响应时间**：< 50ms
- **磁吸开关采样率**：100 Hz（BMM150，10ms 周期）/ 500 Hz（QMC6309，2ms 周期）
- **校准稳定检测时间**：500ms（BMM150）/ 2000ms（QMC6309）
- **单击检测时间窗口**：< 300ms（BMM150）/ < 250ms（QMC6309）
- **串口通信速率**：115200 bps
- **内存占用**：~80KB RAM，~200KB Flash

## 版本历史

### v1.1.0
- **新增功能**：
  - 喂鱼事件检测（FISH_ATTACHED）
  - 配对模式检测（PAIRING）
- **优化改进**：
  - 磁吸数据校准流程全自动化（无需手动提示）
  - 时间基准稳定性检测（FreeRTOS Tick 计时）
  - 单击事件检测优化：基于 mag_x 轴 + mag_x/mag_y 关系判断
  - 防止快速滑动误触发单击事件
  - 校准稳定时间优化：BMM150 降至 500ms
  - 智能位置识别（自动判断位置差异）

### v1.0.0
- 步进电机基础控制
- 多种预定义动作
- 磁吸滑动开关事件检测
- 自动校准功能
- NVS 数据持久化
- 串口通信协议
- 智能跟随鼓点算法
- 长按 Boot 键重新校准
- 校准过程串口通信

## 许可证

本项目遵循 GPL 3.0 许可证。

---

**注意**：本项目为乐鑫"喵伴"智能设备配套固件，仅供学习和参考使用。
