# E1.43a-6：单页实机实验与恢复

**结果：实验已执行并完成恢复，探针未通过。** 2026-11-03，COM4 已恢复原 E1.42 App 和测试页，32 MiB 写前/恢复后快照逐字节一致，原 App 正常启动。用户已确认屏幕、菜单、摸头和对话交互正常，见[独立验收记录](expression-upper-restored-acceptance-20261103.md)。完整实验结果见[本地证据审计报告](../artifacts/expression-upper-live-e1-43a6-20261103/report.json)。

## 已完成与保留证据

- 执行入口：`scripts/run-expression-upper-experiment-e1-43a6.py`。用户确认固定计划后，已完成 `preflight`、`execute` 和自动恢复，进程退出码为 2。
- 首次目录 `tmp/e143a6-live-20261102`：App 的第四个读取块在 `0x00310000` 报 `Corrupt data, expected 0x1000 bytes but received 0xed5 bytes`。进程以非零状态结束，没有生成通过记录、恢复日志或写入 payload；原 App 已重新启动。部分文件和日志保留，不能当作完整快照。
- 完整目录 `tmp/e143a6-live2-20261102`：写前重新采集 App、测试页和全部保护范围。`result.json` 为 `probe_failed_but_restored`，`restored=true`、`journal_finalized=true`、`error=null`；`recovery.json` 为 `complete`、序号 11。
- CPU 日志：`captures/263-upper-probe.log`。恢复启动日志：`captures/379-normal-boot.log`，六项 App/LCD/触摸/PSRAM/OPI 启动标记齐全，无 panic/assert；当时仍在扫描 Wi-Fi。

原始日志、设备身份和回退二进制保留在私有 `tmp/`。本轮只访问 COM4，实际写入范围为下表的 App 与测试页。

## 固定计划与回退边界

计划 ID：`3ca2a150e65e9fe9ff66387ed68cf59d1c096ef3ca0e611986658c8fd6a80671`。

| 输入 | 字节数 | SHA-256 |
| --- | ---: | --- |
| 原 App 擦除并集 `0x00010000..0x00458000` | 4,489,216 | `8a6a323569538c48683006c5baa400054dec990eff5ce25f6c27def826b6d633` |
| 原测试页 `0x01000000..0x01010000` | 65,536 | `71189f7fb6aed638640078fba3a35fda6c39c8962e74dcc75935aac948da9063` |
| 临时诊断 App | 4,486,768 | `7ff00081eb9a89fcc37a5f029c34a15d0c256f51521a4104c77ce6f6815fdced` |
| 固定非 FF 图样 | 65,536 | `63cd7bb2d9acd763bec3d74f3561ac68572f3f45fdfc8c4fc6513d7f6048df22` |

`plan/manifest.json` 和四份二进制均已保存并再次校验。bootloader、分区表、资源、模型和 NVS 本轮前后字节相同；COM8/C61 未访问。

## 实测结果

| 检查 | 结果 |
| --- | --- |
| 图样与临时 App 写入回读 | 与计划字节一致 |
| CPU direct read / mmap | 16 个扇区标记、逐块摘要及整页 SHA-256 一致 |
| 跨 16 MiB 边界 | 两条读取路径内容相同，状态均为 `ESP_OK` |
| 映射释放 | 空闲页 `189 → 188 → 189`；跨界映射 `189 → 187 → 189` |
| 低地址保护摘要 | 与同轮写前快照一致，与历史参考不同 |
| App 与测试页恢复 | 4,489,216 B + 65,536 B 逐字节一致 |
| 完整保护核对 | 写前/恢复后共 32 MiB 逐字节一致，NVS 本轮未变化 |
| 人工交互 | 用户确认屏幕、菜单、摸头、对话及对话中/后切菜单正常；见独立验收记录 |

## 未通过原因

固件的 `kExpectedLowProtectedSha` 和主机校验器仍使用旧全片镜像的低地址摘要；本轮写前的 bootloader 字节已与那份历史镜像不同。CPU 读到的是本轮原有内容。

| `0x00000000..0x00009000` | SHA-256 |
| --- | --- |
| 同轮写前快照与 CPU 结果 | `5da21e331ac861fb542150b060b26ce69bdea0c0b9ecd8a9852d55a4e5755389` |
| 历史参考 | `40d54294f175b8bdcac9057bfcc927517f6a5f4cbf2410bf03d368190cc21dbe` |

两份前缀共有 15,581 B 不同，范围 `0x2..0x3F6F`，均位于 bootloader 区；分区表相同。失败项为 `immutable_lower_prefix_matches_reference` 和受它影响的 `firmware_check_passed`。本轮保留原始失败结果。

## 本轮主机修正及检查

只修改主机适配器和专项测试，没有修改或重建固件：`read_flash` 遇到上面明确的截断错误时，同块最多三次尝试；每次新建证据文件，重试前重新确认设备身份。普通错误、身份差异、成功返回但长度/摘要不符、以及所有写入错误均不走此重试。

适配器专项 26/26 通过，包含截断重试、次数上限、普通错误不重试、身份漂移中止和写入错误不重试；相关 Python 编译检查通过。测试从实际 Git 根用固定 Offline Python 的 `-m unittest discover -s tests -p test_storage_upper_experiment_esptool.py` 执行。原事务控制器 19/19 与入口 8/8 的离线证据保留，不把模拟测试当作硬件通过。

本次遇到的测试导入、WMI 串口枚举空结果和读包截断处理已加入[架构行动指南](architecture.md#工具链环境行动指南)及[开发主线](development-pipeline-20261102.md)。

## 本地审计与下一步

`scripts/audit-expression-e1-43a6-live.py` 只读取保留文件，复核计划、全部分块、两次恢复回读、原始 CPU 记录和启动日志；`--check` 可复算报告。已完成的 run 保持封存，后续使用新 run-label。

[E1.43a-7 同轮快照修正](expression-upper-snapshot-e1-43a7-20261103.md)已完成代码和 133 项专项测试，下一步是默认关闭/启用的独立构建，再生成新的单页实验计划。单页探针及恢复后的交互验收通过后，进入 E1.43b 连续 8 MiB mmap；随后原样迁移 E1.18，再接入单个新表情。
