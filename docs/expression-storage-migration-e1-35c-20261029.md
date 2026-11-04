# E1.35c 实机备份与迁移闸门

更新：2026-10-29。本阶段已访问 COM4 进行**只读**身份确认和 Flash 回读；没有写 Flash、没有擦除、没有写 NVS、没有访问 COM8。

## 设备身份与容量

- 目标：COM4，本体 ESP32-S3，N32R16。
- esptool 检测物理 Flash：32 MiB；启动日志报告 16 MiB PSRAM。
- 当前固件头和当前分区表仍按 16 MiB 配置。启动日志明确提示：检测到 32 MiB，大于镜像头部 16 MiB，因此继续按镜像头使用 16 MiB。
- 未启用 Secure Boot 或 Flash Encryption。
- 当前分区表 SHA-256 与旧布局锚点一致：payload `f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c`，整扇区 `5a0251d24335255b36e5993d5a2a260abb638c29ef02a480094ae26490da477c`。

## 回读证据

完整 32 MiB 采用 1 MiB 分块读取，任何块都要求设备 MAC、长度、传输摘要和本地 SHA-256 一致；失败块可断点重试。全部 32 块完成后，esptool `verify_flash --flash_size keep` 独立校验通过。

- 原始回读：`tmp/e135c-live-20261029/body-full-32MiB.bin`
- 分块清单与重试日志：`tmp/e135c-live-20261029/capture-index.json`、同目录 `chunk-*.bin` / `read-*.log`
- 完整回读 SHA-256：`21e68e726db1e6c4a9b50c05f2dc1505a049feda5d0f6dbc872a67c2bddb1f6e`
- 独立校验日志：`tmp/e135c-live-20261029/verify-full-keep.log`

现场九段连续覆盖 `0x000000..0xEB4000`，并已保存各段摘要。当前 App 有效内容、E1.18 assets 和分区表均与 E1.35b 的旧布局参考一致：

- App payload：`f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2`
- E1.18 assets 分区：`d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6`
- 当前 model 有效 payload：`cec059b2f1f574523e7e53d34166b224f6ae2f393652515ae7a1ef94ed44d7ba`

## 发现与停止点

实机 model 的有效 582,107 字节与构建参考一致，但 800 KiB model 分区的尾部含真实历史字节；实机 bootloader 也与 E1.35a 构建参考不同。因此不能把 E1.35b 的构建参考镜像直接当作设备回退，也不能现在提交候选分区表。

32 MiB 物理容量是可用空间事实，不等于可以立即把资源区扩到 8 MiB。当前应用、分区表、资源加载和 `esp_partition_mmap` 仍按 16 MiB 配置；必须先按实机尾部生成新的设备优先迁移清单，再做独立构建和运行时 mmap/堆/PSRAM 证据。

启动回归日志确认 LCD/LVGL、CST816S、双通道触摸、双麦音频、DOA、底座 UART 和观察工具完成注册；本轮没有做交互验收，也没有改动设备。

## 下一步

1. 以 `body-full-32MiB.bin` 为唯一现场回退来源，重建设备优先的 old/candidate manifest；保留实机 bootloader、NVS、App、assets、model 尾部和 `0xC90000..0xEB4000` 覆盖范围。
2. 重新生成 32 MiB 物理 Flash 下的候选分区表，确认应用和资源组件的容量字段、mmap 页范围和 model 地址；不沿用旧 16 MiB flash map。
3. 做独立的候选构建、分区/资源/模型差异和负向测试。只有这些通过，才进入“迁移授权”询问；本阶段没有获得或执行迁移授权。
4. 迁移后才采集实际 mmap、模型加载、堆/PSRAM 峰值，并进行屏幕、菜单、对话、摸头、睡眠/唤醒和官方行为回归。底座维修后再验收 C61。

状态：`device_backup_complete_candidate_rebuild_required`；`flash_authorized=false`；`device_written=false`。
