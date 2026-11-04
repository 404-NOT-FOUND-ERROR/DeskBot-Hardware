# E1.26 实机部署记录 / Live deployment record

日期：2026-10-27。目标：COM4 本体 ESP32-S3，MAC `9c:cc:01:fb:a8:78`。

## 已完成

- 仅写入 App 起始地址 `0x10000`，写入候选 `xiaozhi.bin`，长度 `4,477,280` 字节。
- 未写入 COM8/C61、分区表、E1.18 表情资源、语音模型或 NVS；未执行 `erase-all`。
- 写后完整回读范围 `0x10000..0x455160`，长度 `4,477,280` 字节，SHA-256 与候选一致：`f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2`。
- 分区表和资源分区使用 `verify_flash` 只读校验通过，分别保持写前摘要。
- 启动日志确认应用进入 `app_main`，LCD/LVGL、CST816S、官方双通道触摸、双麦音频、DOA、底座 UART 和 E1.26 观察工具均完成注册。

## 证据

- 机器记录：[live-deployment-record.json](../artifacts/expression-e1-26-live-deployment-20261027/live-deployment-record.json)
- App 回读文件：本机 `artifacts/expression-e1-26-live-deployment-20261027/app-after-0x10000-0x445160.bin`
- 启动日志：本机 `logs/expression-e1-26-live-deployment-20261027/boot.log`
- 受保护分区校验日志：本机 `logs/expression-e1-26-live-deployment-20261027/protected-verify.log`

当前状态是 `accepted`。用户已确认本体回归正常；该验收覆盖本次 App-only 观察器部署与官方行为回归，不把观察器记录扩大解释为 EAF 加载、LCD 播放或动作完成证据。

## 用户实机验收 / User acceptance

用户确认本次启动后本体运行正常。E1.26 观察器部署没有破坏官方产品路径；后续仍需通过只读 `self.expression.observation` 取得真实调用快照，再导入 E1.27 工作台审阅表情语义和调用顺序。底座 COM8 未写入，底座维修后的 C61 验收仍独立进行。
