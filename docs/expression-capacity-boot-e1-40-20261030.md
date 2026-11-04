# E1.40：成对容量启动只读预检（写前快照）

日期：2026-10-30。状态：**本页保留写前预检；实机结果见 [E1.40 实机结果](expression-capacity-boot-e1-40-live-20261030.md)**。

本轮消费一份刚从 COM4 读取的 32 MiB 全片，以及 E1.39 已审计的显式 Octal 32 MiB bootloader/App。只读预检结束后通过 RTS 发出一次 `hard_reset`，让本体离开 ROM 下载模式；没有执行 `write_flash`、擦除或 COM8 访问。该复位命令成功返回，但没有采集到本次启动日志，因此不据此声称产品已正常启动。

机器报告：[report.json](../artifacts/expression-capacity-boot-e1-40-20261030/report.json) · 最小清单：[deployment-manifest.json](../artifacts/expression-capacity-boot-e1-40-20261030/deployment-manifest.json)

## 现场基线

| 项目 | 结果 |
| --- | --- |
| 端口 / 芯片 | COM4 / ESP32-S3 v0.2；本机日志核实了设备身份，具体标识不发布 |
| 物理 Flash | MXIC `C2:8039`，32 MiB，eFuse Octal，3.3 V |
| 当前 bootloader | 16 MiB 镜像头，16,240 B，`2e9dceaa…3a8a4d` |
| 当前 App | E1.26 已验收 App，16 MiB 镜像头，4,477,280 B，`f95a762d…a029c2` |
| 全片回读 | 33,554,432 B，`7597eb8e…ee58e` |
| E1.18 资源分区 | 全分区 `d0858a38…cada6`，与已验收活动包一致 |
| 语音模型分区 | 全分区 `37a572cd…eed39`，payload `cec059b2…4d7ba` |
| 上半区 | `0x1000000..0x2000000` 全部 `0xFF` |

NVS、OTA data 和 PHY 的摘要按这次现场快照记录；它们没有被当作历史快照来替代。当前状态仍是旧的 16 MiB 配置，成对启动候选尚未运行验证。

## 只写两份镜像

| 文件 | 地址 | 实际长度 | 擦除扇区范围 |
| --- | ---: | ---: | --- |
| E1.39 `bootloader.bin` | `0x000000` | 15,504 B | `0x000000..0x004000`，16,384 B |
| E1.39 `xiaozhi.bin` | `0x010000` | 4,476,944 B | `0x010000..0x456000`，4,481,024 B |

候选 bootloader SHA-256 为 `1b21ce72…c0d0485`，候选 App SHA-256 为 `fa57fac6…352e4e5`。候选仍使用旧分区表和旧资源地址；通用 E1.39 `flasher_args.json` 不作为刷写输入，避免误写资源、模型、OTA 或分区表。

写前必须重新确认端口、MAC、Flash 身份和两份候选摘要。获得用户单独确认后，才允许执行这两个地址对；不生成自动刷写脚本。

## 回滚与保护

回滚片段从本次全片现场回读直接切出，并逐字节复核：

| 片段 | 长度 | SHA-256 |
| --- | ---: | --- |
| `tmp/e140-capacity-boot-20261030/rollback/bootloader-live-sectors.bin` | 16,384 B | `ded3fb6c…2abd09` |
| `tmp/e140-capacity-boot-20261030/rollback/app-live-sectors.bin` | 4,481,024 B | `e63d4758…74dd3` |

保护范围为分区表 `0x8000`、NVS `0x9000`、OTA data `0xD000`、PHY `0xF000`、资源 `0x5EC000`、模型 `0xBC8000` 和上半区 `0x1000000`。清单明确排除 COM8/C61。`flash_script_generated=false`、`flash_authorized=false`、`deployment_eligible=false`。

## 下一闸门

收到明确刷写确认后：

1. 再做一次只读身份和候选哈希核对。
2. 仅写 bootloader 与 App 两个地址，随后分别回读实际擦除范围。
3. 检查分区表、NVS、资源、模型、OTA 和上半区保护摘要未变化。
4. 收集正常启动日志，确认 32 MiB OPI_STR 初始化、EAF 挂载和语音模型加载。
5. 由用户验收显示/菜单、对话、对话中切菜单、摸头、睡眠唤醒、DOA 和资源播放；通过后才把 E1.40 标记为 accepted。

本页记录的是写入前的证据边界，不能单独用来判断设备当前状态。写入、回读和用户实机验收已记录在 [E1.40 实机结果](expression-capacity-boot-e1-40-live-20261030.md)。
