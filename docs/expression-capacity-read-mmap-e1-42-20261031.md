# E1.42 Flash 边界只读验证

## 目标与范围

验证 ESP32-S3 N32R16 在现行 32 MiB Octal OPI_STR、Octal cache32 配置下，CPU 是否能在 16 MiB 边界两侧执行 `esp_flash_read` 与 `spi_flash_mmap`，以及映射释放后 MMU 页面是否恢复。工具 `self.probe_flash_addressing` 只读两个 32 字节样本、短暂映射不超过两个 64 KiB 页、采集 internal heap/PSRAM 指标，并在物理容量末端做一次越界读取拒绝测试；它不写 Flash，不修改 NVS、assets、model 或 C61。

低侧样本为 `0x00FFFFF0..0x01000010`，跨越 16 MiB；对应映射从 `0x00FF0000` 开始、长度 128 KiB。高侧样本从 `0x01000000` 开始，对应映射 64 KiB。IDF 5.5.5 源码确认从 `chip->size` 起读 1 字节返回 `ESP_ERR_INVALID_ARG`。固定地址访问仅在驱动报告恰好 32 MiB 时进行。

## 固定参考

参考镜像是 E1.40 USB stub 全片回读 `tmp/e140-capacity-boot-20261030/current-full-32MiB.bin`，大小必须正好 33,554,432 字节。已独立查看边界两侧样本各 32 字节均为 `0xFF`，完整 `0x01000000..0x02000000` 上半区均为 `0xFF`。USB stub 回读仅作为已知数据样本参考，不作为 CPU 高地址映射证据。构建报告记录参考镜像哈希。

拿到设备原始 JSON 后运行：

```powershell
& "D:\Program Files (x86)\Python\Python 3.13.0\python.exe" .\scripts\validate-expression-e1-42-probe.py .\artifacts\expression-capacity-read-mmap-e1-42-20261031\probe.json
```

校验器会核对 schema、地址/长度、状态码、结果布尔值、实际映射页计数与恢复声明，并把两个样本和 E1.40 全片回读逐字节比较。它拒绝 Flash 尺寸不符、写入声明、错误码不一致、映射页未恢复或样本偏离参考的结果。

## 离线与构建状态

构建前先执行 `scripts/preflight-deskbot-toolchain.ps1` 的 Offline 与 Build 模式。构建脚本以 E1.41 报告为基线，只允许固件源码中的 `dev_tools.cc` 出现预期增量；bootloader、分区表、EAF 资源、语音模型及有效 sdkconfig 必须与 E1.41 哈希一致。构建输出保存在隔离 `tmp/e142-flash-probe-20261031`，App 分区至少保留 1 MiB 余量。

当前状态：源码/API 审计、离线测试、源码清单、隔离构建和 COM4 App-only 写入回读已完成。候选 App 为 4,481,776 B，写入地址为 `0x10000`，回读 payload SHA-256 为 `14bc81889621bc8cd9f9c34077ade062231a964fb0e9efcdbbccdfdf296e2f1d`，实际回读长度 4,485,120 B，尾部 3,344 B 全为 `0xFF`，写入日志包含 `Hash of data verified`。用户确认本体“一切正常”。设备启动日志、真实 CPU mmap/read 探针 JSON、主机 `tools/list` 分页和 `tools/call` 返回仍未取得；构建/刷入通过不代表 CPU mmap 或 MCP 工具验收。部署范围和未闭合项见[机器记录](../artifacts/expression-capacity-read-mmap-e1-42-20261031/live-result.json)。

E1.42.1 的静态 MCP 可见性审计见 [mcp-visibility-static.json](../artifacts/expression-capacity-read-mmap-e1-42-20261031/mcp-visibility-static.json)。源码确认五个目标工具按 ESP-Vocat 专用段顺序注册，均使用普通 `AddTool`；`tools/list` 默认只排除显式 user-only 工具，并支持 8,000 字节分页。源码的 `nextCursor` 指向因当前页容量不足而尚未加入当前页的第一个工具；后续页从该工具开始，所以静态逻辑不会造成页间重叠，不应按上一轮错误结论额外去重。静态审计不代替真实 `tools/list` 或 `tools/call`，因此主机状态仍为 pending。

## E1.42 部署记录

| 项目 | 结果 |
| --- | --- |
| 目标 | COM4，本体 ESP32-S3 N32R16 |
| 写入 | 仅 App `0x10000`；未写 bootloader、分区表、assets、model、NVS、COM8/C61 |
| App | 4,481,776 B；擦除/回读范围按实际写入长度保存于 `tmp/e142-flash-probe-20261031/` |
| 校验 | esptool `Hash of data verified`；回读 payload 与候选摘要一致 |
| 人工回归 | 用户报告本体基础交互正常；这不替代跨界探针数据 |
| 工具状态 | `self.probe_flash_addressing` 已在固件中注册；主机可见性和真实调用仍 `pending` |

## 验收边界

- 通过：驱动报告 32 MiB；上下 `esp_flash_read` 与 CPU mmap 字节一致并匹配 E1.40 参考；上半区样本仍为 `0xFF`；越界读取返回 `ESP_ERR_INVALID_ARG`；映射释放且页面计数恢复；写入声明为 false。
- 未通过：保留完整 JSON、状态码、页面和内存数据；不进入资源迁移。核实 MCP 主机列表/调用后，再判断配置、芯片驱动或运行时内存问题。
- 仅写 COM4 App 分区 `0x10000`；不写 bootloader、分区表、assets、model、NVS、COM8/C61，不全片擦除。
- 通过后下一阶段才设计 8 MiB 活动资源布局迁移，重新比较 MMU 连续映射、模型位置和断电回退；本阶段不迁移资源。
