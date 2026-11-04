# E1.40：成对容量启动实机结果

部署日期：2026-10-30；用户验收日期：2026-10-31。状态：**旧分区布局容量启动已通过实机验收**。

这次按用户授权只写 COM4 本体的两个地址：bootloader `0x000000` 和 App `0x010000`。没有访问或写入 COM8/C61，没有擦除全片，没有覆盖分区表、资源、语音模型或上半区，也没有通过刷写修改 NVS。

## 结果

| 闸门 | 结果 |
| --- | --- |
| bootloader 写入 | 15,504 B，`Hash of data verified` |
| App 写入 | 4,476,944 B，`Hash of data verified` |
| bootloader 实际擦除范围回读 | `0x000000..0x004000`，候选 payload 一致，尾部 `0xFF` |
| App 实际擦除范围回读 | `0x010000..0x456000`，候选 payload 一致，尾部 `0xFF` |
| 启动模式 | `mxic (opi)`、`flash io: opi_str` |
| 设备初始化 | LCD、触摸、EAF 索引、语音模型均成功 |
| 用户回归 | 屏幕、菜单、对话中/后切菜单、摸头、睡眠/唤醒全部正常 |

完整哈希和日志索引见 [live-result.json](../artifacts/expression-capacity-boot-e1-40-20261030/live-result.json)。原来的 [预检报告](../artifacts/expression-capacity-boot-e1-40-20261030/report.json) 保留为写前快照，不覆盖其 `flash_pending` 结论。

## 保护区说明

分区表、OTA data、PHY、assets、speech model 和 32 MiB 上半区回读与写前快照一致。NVS 没有被刷写命令触碰，但刷后保护区回读时有 29 个字节变化。使用 ESP-IDF 5.5.5 自带 NVS 解析器核验：两份快照均为 92 个有效条目，CRC 均通过；唯一变化的有效条目为 `audio.output_volume`，从写前 80 变为回读时的 100，其余有效条目逐字节一致。变化的触发动作和准确发生时间没有被捕获。

稍后的 30 秒启动采集又记录了 `self.audio_speaker.set_volume` / `Set output volume to 80`。这表明该工具会走应用音量设置路径，但不能用这个较晚的日志推定前一次 80→100 的原因或时间。原始 NVS、备份和包含网络信息的日志仅保留本机，不发布。

启动日志还包含资源挂载前的图标查询失败、缺少 `microchip_ai`、24 kHz→16 kHz 重采样和两条 MQTT 音频序号警告；没有采集到断言、panic、brownout 或重启循环。没有为这些诊断项额外修改固件。

## 边界

E1.40 证明的是两份 32 MiB 配置镜像在旧分区表下以 OPI_STR 成功启动和本体回归；本轮启动日志未直接输出驱动容量查询结果，也没有 CPU 上半区 read/mmap 结果。下一阶段先直接查询驱动容量，再做跨 16 MiB 边界的 read/mmap、释放路径和内存观测；通过后再单独规划 8 MiB 资源布局迁移。USB stub 回读上半区不能替代 CPU mmap 验证。底座维修后的 C61、电机、磁吸和底座事件仍不在本次验收内。
