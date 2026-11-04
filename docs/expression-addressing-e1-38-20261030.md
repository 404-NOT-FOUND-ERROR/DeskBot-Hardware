# E1.38：寻址兼容性与精确现场回退

日期：2026-10-30。状态：`offline_preflight_complete_addressing_candidate_blocked`。

本轮完成前序路线的第一步：消费已有 E1.35c 全片快照和 E1.37 双容量构建，核对 Flash 模式、驱动容量来源、上半区 mmap 条件和实际擦除扇区的精确回退。没有新构建、串口访问、重启、刷写、C61 或 NVS 操作。设备的已验收版本记录仍为 E1.26 App + E1.18 assets。

[可视化审阅页](../artifacts/expression-addressing-e1-38-20261030/index.html) · [机器报告](../artifacts/expression-addressing-e1-38-20261030/report.json)

## 关键结论

| 层次 | 本轮证据与结论 |
| --- | --- |
| 物理 Flash | JEDEC `C2:8039`，Macronix/MXIC，32 MiB；eFuse 为 Octal、3.3 V。准确商业料号尚未确认，不根据 JEDEC 家族虚构型号 |
| 运行模式 | 2026-10-29 正常启动日志为 `mxic (opi)` / `opi_str`；既有固件依 eFuse 自动从 QIO 选择切到 OPI_STR |
| 镜像与编译配置 | 镜像头为 DIO；E1.37 的有效编译选择为 QIO + auto-detect，`ESPTOOLPY_OCT_FLASH=false`。因此历史“Quad Flash”只能描述编译配置，不能描述物理芯片 |
| 容量初始化 | `SPI_FLASH_SIZE_OVERRIDE=false`，运行驱动按 bootloader 头限制容量。32 MiB App + 16 MiB bootloader 仍预期限制为 16 MiB；单刷 App 不能证明扩容 |
| 上半区 mmap | App 与 bootloader 的两个 `CACHE_32BIT_ADDR` 开关均关闭。当前 `flash_mmap.c` 会拒绝起点 ≥16 MiB 或跨越该边界的映射；运行时自动识别不会补开编译时 guard |
| EAF 挂载 | 当前 `Assets::EmoteStrategy::InitializePartition` 明确要求 `.mmap_enable=true`；USB stub 全片回读成功不等于动画资源可从上半区映射 |
| 回退 | 已从现场全片切出 E1.37 成对容量候选所需的完整擦除扇区；构建 bootloader 不作为精确回退。该范围不自动适用于下一份 Octal 候选 |

这一步审计完成，但现有 E1.37 候选不具上半区资源部署资格。硬件存在 32 MiB 与“所有 32 MiB 已能由播放器使用”是不同的结论。

## 源码依据与复核方式

报告固定了十个本机文件的 SHA-256、十一处行锚点和最小相关摘录。IDF 版本为 5.5.5；固定的是实际审阅文件，不能据此宣称整个本机 IDF 树为纯净发行版。

| 依据 | 本次检查 |
| --- | --- |
| `components/bootloader/Kconfig.projbuild` | 32 MiB 派生寻址配置；Quad cache32 依赖实验选项；Octal cache32 由显式 Octal + 32 位容量派生 |
| `components/spi_flash/esp_flash_spi_init.c` | eFuse 自动选 OPI_STR；探测物理容量后按 ROM/legacy chip size 限制运行容量 |
| `components/spi_flash/Kconfig`、`cpu_start.c` | 默认使用 bootloader 头；只有开启 size override 才采用 App 头 |
| `bootloader_flash_config_esp32s3.c` | 从 bootloader 头配置 ROM flash size |
| `spi_flash_chip_mxic_opi.c` | MXIC OPI 探测、容量解码和 32 MiB capability；这不是整机大地址缓存的实测 |
| `flash_mmap.c` | 24 位映射范围 guard |
| `esp_bootloader_desc.h/.c` | bootloader descriptor 的 24 B 编译时间字段 |
| `firmware/body/main/assets.cc` | 官方 EAF 分区挂载要求 mmap |

本机审计先执行 E1.37 `--check`，重新验证源文件/组件锁、有效配置、构建 checkpoint/log、二进制和摘要，再验证 32 个读取分块、拼接全片及独立全片 verify 证据。公开检查只核对脱敏元数据、仓库源文件和页面，不冒充重新读取了私有全片、IDF 或回退字节。

## 精确现场回退

现场有效 bootloader 为 16,240 B，SHA-256：

`2e9dceaa1affbc6e38826dbc320a08fbc46b5e55be21be043fc060cf13da8a4d`

与 E1.37 的 16 MiB 构建版相差 53 B，与 32 MiB 构建版相差 54 B。差异分别为编译时间字段中 20 B、校验 1 B、SHA-256 32 B；32 MiB 另有容量头 1 B。段几何、其他 descriptor 字节、其余代码与数据经过逐字节检查，不允许通过“忽略日期”掩盖代码变化。即使变化都能解释，两个构建文件仍不等于现场逐字节回退。

| E1.37 条件候选 | 候选有效字节 | 实际扇区范围（末端不含） | 回退字节 |
| --- | --- | --- | --- |
| bootloader | 16,240 B | `0x0..0x4000` | 16,384 B |
| App | 4,477,280 B | `0x10000..0x456000` | 4,481,024 B |

回退片段与清单只保留在本机项目 `tmp/e138-addressing/`。它们来自历史现场原字节，包含有效镜像之后的擦除影响范围；不把 build 文件补 FF 当作回退。新候选长度变化必须重算扇区。完整 32 MiB 备份继续保留；不能清理为只剩这两段。

保护范围摘要覆盖全片，包括 bootloader 预留区、分区表、NVS、otadata、PHY、App、当前 E1.18 assets、独立 model、原位方案尾部和上半区。当前资源全分区 SHA 为 `d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6`；model 全分区 SHA 为 `37a572cda25d8381b733f8ce48c1532488c87cf3359e1c78b13b1262f58eed39`。model 尾部仍必须按现场恢复，不能用构建 model 覆盖。

报告明确 `live_snapshot_current=false`：这些是 2026-10-29 的快照，不是本轮实时设备状态。任何写入前都须重新识别端口/芯片、刷新受影响范围和保护范围摘要；若现场已变化，重新备份并重建精确回退。此次没有生成可执行刷写脚本，也没有获得新的写入授权。

## 验证

可移植专项：

```powershell
python tests/test_storage_addressing.py -v
python scripts/run-offline-checks.py
python scripts/check-publication.py
python scripts/check-workbenches.py
node tests/expression-e1-38-browser.cjs
git diff --check
```

本机保留证据复核需已初始化的 ESP-IDF Python 环境（含 PyYAML）和此前的私有构建/快照；没有文件即失败，不联网补造证据，也不访问设备：

```powershell
python scripts/audit-expression-e1-38-addressing.py --check
```

生成器只在非 `--check` 模式写本机回退片段和脱敏审阅产物；`--check` 全程核对现有内容。测试覆盖模式混淆、App-only 容量误判、边界映射、校验损坏、真实代码变化、完整扇区覆盖、FF 假回退、重叠范围、路径逃逸以及伪造部署/实机状态。

## 下一步：E1.39

本轮验证结果：专项 **20/20**；完整 portable 套件 **337 项、335 通过、2 条件跳过**；本机全片/构建/IDF/精确回退 `--check` 通过；公开发布检查 1,503 文件通过；工作台 **23 页、22 段 JS、22 份 JSON** 及回环 HTTP 通过；Edge 桌面 1440×1000、手机 390×844 的表格、可展开源码、无全页溢出/脚本错误/外部请求检查通过；`git diff --check` 通过。没有增加设备测试或验收结论。

1. 在独立工程副本审阅并构建显式 Octal、32 MiB 候选，与现有 E1.37 配置对照。核对自动派生 cache32、驱动/读写指令和启动路径；不直接强开 Quad 实验配置。
2. 保持旧表、E1.18 资源、两条模型路径、PSRAM 参数和产品行为。配置变化与镜像段差异须逐项说明；新 bootloader/App 长度重新计算现场回退范围。
3. 只有兼容性与预检满足后，独立安排容量启动验证；刷新现场备份后单独授权，记录运行驱动容量与官方本体回归。此轮仍沿用旧资源地址，不引入新动画。
4. 容量启动稳定后，再做上半区读/mmap、8 MiB 资源布局、模型和内存峰值验证；之后恢复角色活动包、节点替换与客户端链路。底座维修后的动作、DOA 与外壳识别另验。
