# 当前基线与验证状态 / Baseline Status

更新：2026-10-23。此文件取代此前 R9 作为“当前版本”的说明，历史提交保持可追溯。

## O2f2 App：已验收 / Accepted

| 项目 / Item | 记录 / Value |
| --- | --- |
| 上游 / Upstream | `esp-friends/esp-vocat` @ `46d427255756fe2a148777ea3f256b615b52e160` |
| 本体 / Body | ESP-VoCat V1.2 / ESP32-S3 |
| 底座 / Base | 独立 ESP32-C61，非同一固件 / separate firmware |
| App 起始 / Offset | `0x10000` |
| 有效 App / Image size | 4,472,496 B |
| App SHA-256 | `4be89c2533909d06e9a8ef43a2aaa67ce9ec1e5a70a762f2827bf4388ac880a5` |
| App 分区 / Partition | `0x5DC000` / 6,144,000 B，剩余 1,671,504 B |
| 分区表 SHA-256 / Table hash | `f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c` |

用户已确认菜单、点击对话、对话中/后菜单切换、摸头、睡眠/唤醒与转头等基线能力正常。只读工具为 `self.echo_base.events`、`self.shell.identify`、`self.shell.list`。档案仅在 RAM，不声称重启保留；没有研究设置页。历史 R9 的 UI 冻结不作为已解决版本复用。

User acceptance belongs to the recorded local O2f2 App, not automatically to a future rebuild at the public path. Publication updates source inputs and documentation; it does not flash a device or certify a new binary.

## E1.13 assets：完整性通过、交互待验收

| 项目 / Item | 记录 / Value |
| --- | --- |
| 唯一写入分区 / Only written partition | assets `0x5EC000..0xBC8000`，长度 `0x5DC000` |
| 修改 / Changes | `insert.eaf`、`eat.eaf` 无损重编码；原视觉不变 |
| 成员数 / Members | 29，名称集合不变 |
| 原包 / Original package | 5,889,001 B |
| 候选包 / Candidate package | 5,538,349 B |
| 释放容量 / Reduction | 350,652 B |
| 当前余量 / Remaining | 605,651 B / 6,144,000 B |
| 原始包 SHA-256 | `56b39e7fdd14b9dd0de9b22d3aedc9b04383376dbdef70925675f639d763cd5b` |
| 候选包 SHA-256 | `5e342db35da13531836339a98c25d6e74ac6fdff446ad4e515fcf39f28b7e81f` |
| 完整分区与回读 SHA-256 | `6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205` |

写入器校验、完整分区回读逐字节比较、O2f2 App verify、分区表摘要复核通过。25 秒启动采集未见 panic/WDT/assert，LCD、触摸、双麦/DOA、UART、MCP 初始化正常。这不证明实际触摸、菜单并发、联网对话或运动效果。底座正在维修，`insert/eat` 的真实事件触发也未验收。

The assets write did not modify the App, bootloader, partition table, OTA data, NVS, speech model or C61. Full readback integrity passed; hardware interaction and final release eligibility remain pending. Private backups and raw logs are retained locally and are not published.

## 发布等级 / Status Vocabulary

- `offline_validated`：主机输入/输出已验证，不代表设备执行。
- `built`：历史指定输入已构建，不代表当前公开路径已重新构建。
- `deployed_integrity_verified`：写入与回读一致，不等于交互通过。
- `accepted`：对应版本的用户实机验收通过。

当前：App `accepted`；E1.13 `deployed_integrity_verified / pending interaction acceptance`；客户端链路 `offline prototype`。本次 Git 发布不改变这些等级。
