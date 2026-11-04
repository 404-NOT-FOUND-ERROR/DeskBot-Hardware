# 本体硬件容量档案 / ESP32-S3 N32R16 Hardware Profile

更新：2026-10-30。此文件固定本体容量事实，避免把物理容量、镜像头配置和运行内存混为一谈。

## 已核实的现场事实

| 项目 | 事实 | 证据 |
| --- | --- | --- |
| 芯片 | ESP32-S3，revision v0.2 | `tmp/e135c-live-20261029/flash-id.log` |
| 本体端口 | 历史快照为 COM4；写前重新识别，设备标识仅私有保存 | `flash-id.log`、`capture-index.json` |
| 物理 Flash | **32 MiB / 33,554,432 B** | `Detected flash size: 32MB`；32 个 1 MiB 分块回读 |
| PSRAM | **16 MiB** | 启动日志 `Found 16MB PSRAM device` |
| 当前镜像头声明 | **32 MiB**（E1.40 已部署并完成限定本体回归） | 两份镜像的完整擦除范围回读与已审计 E1.39 32 MiB 镜像一致；[实机记录](expression-capacity-boot-e1-40-live-20261030.md) |
| 当前正式分区表 | 旧 16 MiB 布局 | 分区表 SHA-256 与 E1.35c 现场核对一致 |
| Flash 家族/接口 | JEDEC `C2:8039`，Macronix/MXIC，eFuse Octal / 3.3 V；准确商业料号未确认 | E1.38 重核历史 `flash-id.log` |
| Flash 运行模式 | `mxic (opi)` / `opi_str` | 正常启动日志；不是根据镜像头推断 |
| 镜像/编译选择 | E1.40 采用 E1.39 显式 OPI/STR 32 MiB 成对镜像；运行 OPI_STR | E1.39 有效配置、E1.40 回读与启动日志；驱动容量直接查询和 CPU 上半区 read/mmap 待验 |

## 这几个容量分别代表什么

- **物理 Flash 32 MiB**：芯片实际拥有的非易失存储上限；这是 N32R16 中的 `N32`。
- **镜像头 32 MiB**：E1.40 当前 bootloader/App 的声明容量；E1.39 成对候选通过回读和启动检查。它与物理容量不同，且不代表上半区 CPU 映射已经验证。
- **PSRAM 16 MiB**：运行时内存，不是 Flash 分区，也不能直接当作动画资源空间。

当前正式分区表仍按旧 16 MiB 布局运行，因此上半区不能在没有新分区表、CPU 映射和回退验证的情况下直接用于资源。E1.35c 的完整只读快照记录了当时旧容量配置，其 SHA-256 为 `21e68e726db1e6c4a9b50c05f2dc1505a049feda5d0f6dbc872a67c2bddb1f6e`；该快照不是 E1.40 刷写后的设备镜像。

## 迁移规则

1. 所有容量报告同时写明 `physical_flash_bytes`、`configured_flash_bytes` 和 `psram_bytes`。
2. 旧的 `16m.csv`、`FLASH_BYTES = 16 MiB` 等输入只表示**旧布局边界**，不得改名为物理容量。
3. 16 MiB 镜像可以在更大的物理 Flash 上运行；E1.37 的 16 MiB 控制构建记录了这种历史配置。容量差异本身不说明已有官方功能或资源替换失效。E1.35a/b 的 8 MiB 资源候选仍是研究结果，尚未通过设备运行验收。
4. 利用完整 32 MiB 需要独立核对 bootloader/App 镜像头、Flash 驱动容量、分区表、资源整区 mmap、model 加载、内部堆/PSRAM 峰值、回退范围和断电恢复。E1.35a/b 的构建参考与现场 bootloader/model 尾部存在差异，不能作为当前设备的精确恢复源。
5. E1.40 成对启动及本体回归已验收；继续保持 E1.18 活动资源与官方行为。上半区资源迁移仍须通过独立 mmap/内存闸门并单独授权。
6. `SPI_FLASH_SIZE_OVERRIDE=false` 时，以 bootloader 头为容量来源。只刷 32 MiB App、保留旧 16 MiB bootloader，不能声称运行容量已升级。
7. 自动选 OPI 不会补开编译时 mmap guard。E1.37 的两个 cache32 开关关闭，不能用它证明上半区映射；[E1.39](expression-octal-e1-39-20261030.md) 的显式 OPI_STR 成对镜像现已由 E1.40 启动。CPU 跨界 mmap 和资源迁移仍分别待验。参见 [E1.38 寻址与回退](expression-addressing-e1-38-20261030.md) 与 [E1.40 实机结果](expression-capacity-boot-e1-40-live-20261030.md)。

## 当前结论

2026-09-14 的硬件记录已经列出本体物理 Flash 32 MB，更早记录也区分过 32 MB Flash 与 16 MB 固件配置。后续 E1.35 规划没有沿用这一事实，roadmap 又把“当前固件按 16 MiB 配置”写成“核对 16 MiB Flash”，这是容量规划和文档错误。后续预算以 **N32R16 = 32 MiB Flash + 16 MiB PSRAM** 为硬件事实，并独立注明软件配置。

现场摘要与机器报告见 [E1.35c 只读快照](expression-storage-migration-e1-35c-20261029.md)、[E1.38 寻址页](../artifacts/expression-addressing-e1-38-20261030/index.html)、[E1.39 显式 Octal 审阅](expression-octal-e1-39-20261030.md)和 [E1.40 实机结果](expression-capacity-boot-e1-40-live-20261030.md)。全片、启动/写入日志与精确回退字节只留在本机项目目录。
