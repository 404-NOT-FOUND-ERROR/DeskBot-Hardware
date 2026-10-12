# DeskBot-Hardware

本仓库聚焦一个 ESP-VoCat 桌宠身体和独立旋转底座：保留官方语音、双麦声源跟随、摸头、表情、睡眠/唤醒和电机能力作为回归基线，在此之上逐步验证磁吸外壳识别、底座事件协议、只读诊断页面、表情/动作预览和未来客户端通信。

公开源码快照将新增设备页的两款字体替换为 SIL OFL 1.1 许可的思源黑体派生位图，保留原有字体符号和字号。它因此不保证与本地 R9 App 的二进制哈希相同；字体来源与重新生成方式见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。本地刷写镜像没有因发布工作而改变。

## 重要状态

当前设备最后刷入的是 **R9 display-rollback 实验候选**，不是完整官方基线。R9 只将四个显示链路文件恢复到官方源码，同时保留此前的底座事件、磁场遥测和外壳识别增量；由于用户尚未完成实机回归，本仓库不把它标为“已验收”。官方基线镜像只保存在本地私密备份中，仓库不发布完整 Flash、NVS 或设备日志。

最近一次 R9 构建与刷写记录：

- 上游小智源码锚点：`esp-friends/esp-vocat`，提交 `46d427255756fe2a148777ea3f256b615b52e160`。
- 目标：ESP-VoCat V1.2，ESP32-S3；底座为独立 ESP32-C61。
- R9 App：`0x10000`，4,523,504 bytes，SHA-256 `02FC801F8FA14AA181785717DC36B11E1292C258CD9CB5CA0CD5EE2D4A25852D`。
- 写入范围：仅本体 App；没有擦除 Flash，也没有写入 C61、NVS、分区表、表情资源或语音模型。
- 启动窗口没有出现 `task_wdt`、`Guru Meditation` 或 `wait_for_flushing`；菜单、对话和触摸仍需人工验收。

## 目录

| 目录 | 内容 |
| --- | --- |
| `firmware/body` | 本体 R9 候选的 ESP-IDF 源码、必要资源、固定依赖和构建说明 |
| `firmware/base` | C61 底座的部分源码快照、工作树差异和上游边界说明；不是完整可构建底座 |
| `protocol` | 身体事实、底座事件和未来客户端通信的协议草案 |
| `gateway` | 电脑端网关骨架；API Key 只能通过本地环境变量注入 |
| `tools` | 只读串口诊断、表情能力导出和离线预览工具 |
| `tests` | 不连接硬件的协议、事件、状态和预览测试 |
| `docs` | 架构、基线、路线、磁场识别和安全刷写记录 |

## 开发原则

1. 官方能力先作为不可破坏的基线；每轮只改一个边界清晰的功能。
2. 先做源码差异审计和离线测试，再独立构建、核对分区/资源边界，最后才允许明确端口刷写。
3. 本体 S3 与底座 C61 是两个独立固件；不把底座事件解析、磁场识别或客户端逻辑混成一个 UART 接收器。
4. 磁场高频采样进入遥测和识别模块，不直接刷屏；事件日志只记录状态变化、动作完成、磁吸开关、配件、配对和校准事实。
5. 不在仓库提交完整 Flash、NVS、Wi-Fi 凭据、API Token、构建缓存或设备运行日志。

## 快速开始

先阅读 [`docs/baseline-status.md`](docs/baseline-status.md) 和 [`docs/reproducible-workflow.md`](docs/reproducible-workflow.md)。本体 `firmware/body` 发布完整工程输入快照（不含外部依赖缓存和生成输出），构建需要 ESP-IDF 5.5.x 和锁定的上游依赖；构建脚本只生成镜像，不自动刷写。底座当前只发布部分增量，请先阅读 [`firmware/base/README.md`](firmware/base/README.md)，不能直接套用本体构建入口。

离线测试示例：

```powershell
python .\tests\base_event_protocol_test.py
python .\tests\test-expression-preview.py -v
python .\tests\test-expression-capabilities.py -v
```

## 许可证与上游来源

本仓库新增的研究文档、协议草案和工具按根目录 MIT 许可发布。ESP-VoCat、小智、ESP-IDF 组件、字体与 ESP-VoCat Base 的源文件和资产仍受其各自上游许可证约束；提交、许可证和字体来源见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。根目录 MIT 许可不重新授权第三方内容。

## 当前不包含

完整 Flash/官方固件镜像、NVS、芯片 MAC 对应的运行日志、Wi-Fi/API 凭据、生成的 `build` 目录、ESP-IDF managed component 缓存和客户端私有配置均被排除。本体保留构建所需的上游源码、表情/声音/字体资源和本地触摸组件；这些文件是构建输入，其许可单独记录。
