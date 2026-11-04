# E1.43a-1：已知非 FF 图样与离线门禁

本次完成电脑侧准备，不构建或刷写固件。接续[前置清单](expression-upper-pattern-e1-43-preflight-20261102.md)和[开发主线](development-pipeline-20261102.md)。

## 已实现

- `protocol/storage_upper_pattern.py`：固定 `0x01000000..0x01010000` 的 65,536 B 图样，16 个 4,096 B 块；整页/逐块摘要、标记和严格校验。
- `protocol/storage-upper-pattern-e1-43a.schema.json`：固定版本、范围、标记尺寸、块记录和禁止部署字段；拒绝额外字段、错误类型。
- `scripts/prepare-expression-e1-43a-pattern.py`：生成和只读复核；同名目录拒绝覆盖，先校验全部输入再输出，没有串口/刷写入口。
- `tests/test_storage_upper_pattern.py`：加入可移植套件，覆盖篡改、截断、重复/换序、真假类型、错设备意图、越界/重叠、分区漂移、不可信参考。

图样 SHA-256：`63cd7bb2d9acd763bec3d74f3561ac68572f3f45fdfc8c4fc6513d7f6048df22`。16 块均不同，各自区别于历史低地址对应块；它是地址测试材料，不是 EAF、资源包或新分区。

## 二进制格式 v1

每扇区为 64 B 小端头 + 4,032 B 数据；头格式 `<8s8I24s`：

| 扇区内偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 8 B | `DBUP43A` 加零字节 |
| 8 | 4 B | 版本 1 |
| 12 | 4 B | 页起始地址 `0x01000000` |
| 16 | 4 B | 页长度 65,536 |
| 20 | 4 B | 扇区序号 0..15 |
| 24 | 4 B | 扇区绝对地址 |
| 28 | 4 B | 扇区相对页偏移 |
| 32 | 4 B | 扇区长度 4,096 |
| 36 | 4 B | 数据长度 4,032 |
| 40 | 24 B | `SHA256(domain)` 前 24 B |

`domain = b"deskbot.e1.43a.pattern.v1\0"`。后续数据为 counter=0..125 的 `SHA256(domain + pack('<III', page_start, sector_index, counter))` 拼接。不含时间、随机种子和身份，跨机器可复现。

## 证据与限制

生成器使用摘要锁定的历史 E1.40 全片参考，独立解析旧表条目/MD5/填充，确认范围不重叠，并比较低地址、历史目标页和擦除态。它不是本轮现场备份，不证明当前连接身份、分区或 App。目标元组中的 COM4/芯片/容量只检查意图约束，不能替代现场私有 USB 身份或写入许可。

公开[清单](../artifacts/expression-upper-pattern-e1-43a-20261102/pattern-manifest.json)和[报告](../artifacts/expression-upper-pattern-e1-43a-20261102/report.json)含摘要、16 块标记及 pending 门禁。母文件保留在被忽略的 `tmp/e143a-upper-pattern-20261102/initial/`；没有可执行写入清单。

## 复核入口

从实际 Git 根执行，使用本机已验证的离线解释器；该解释器不用于 IDF/串口脚本：

```powershell
& '.\scripts\preflight-deskbot-toolchain.ps1' -Mode Offline
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' .\tests\test_storage_upper_pattern.py -v
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' .\scripts\prepare-expression-e1-43a-pattern.py --run-label initial --check
```

新的生成使用唯一 run-label；`--check` 不写文件。公开 clone 可跑专项测试；生成器复核还要求保留私有参考与母文件，缺失就失败。

## 本轮检查结果

- 专项 **19/19 通过**；生成器对既有材料 `--check` 通过，图样摘要稳定。
- 完整可移植套件 **426 项：421 通过、2 跳过、1 failure + 2 errors**。三项均为之前已知的 E1.26 冻结源码/构建快照检查：`test_integration_removes_only_the_observer_guard_to_match_baseline`、`test_locked_full_body_report_verifies_both_builds_and_partition_margin`、`test_locked_full_body_report_requires_reproducible_configuration`。当前阶段没有新增失败；未放宽旧检查或改写历史构建报告。
- 发布检查与 `git diff --check` 通过；完整套件不是全绿，不能写为全部通过。[测试结果与日志摘要](../artifacts/expression-upper-pattern-e1-43a-20261102/test-report.json)保留实际分类；原始日志位于私有 `tmp/e143a-upper-pattern-20261102/`。
- 没有构建固件、打开串口、复位或刷写设备；没有为本轮新建大容量构建目录。

下一步单独实现 CPU 固定范围只读探针、新结果格式/解析和 off/on 构建审计，然后新鲜备份→单页写入→整页 read/mmap 摘要和别名保护→精确恢复→人工回归。旧 FF 校验器和历史报告不变。
