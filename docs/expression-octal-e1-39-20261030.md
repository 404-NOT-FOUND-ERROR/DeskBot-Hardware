# E1.39 显式 Octal 16/32 MiB 隔离构建

日期：2026-10-30。目标是把已确认的 N32R16 物理 Octal 接口落实到独立构建配置，并核对容量、缓存寻址与现场回退条件。

[可视化审阅页](../artifacts/expression-octal-e1-39-20261030/index.html) · [构建报告](../artifacts/expression-octal-e1-39-20261030/report.json) · [完整配置/镜像/ELF 审计](../artifacts/expression-octal-e1-39-20261030/review.json)

## 本轮结果

两份真实隔离构建及保留证据审计通过，尚未部署或实机验收。

| 产物 | 16 MiB 显式 Octal 控制版 | 32 MiB 显式 Octal 候选 |
| --- | --- | --- |
| App | 4,477,200 B | 4,476,944 B |
| App 分区余量 | 1,666,800 B / 6,144,000 B | 1,667,056 B / 6,144,000 B |
| App SHA-256 | `698b1a74d698f90fd9f0d1d00049b2002c34380a83da7132d1eb479cb40c3153` | `fa57fac6056be8b779a52e97cd1f7410c69d5af4c1798a52560f631ce352e4e5` |
| bootloader | 15,024 B | 15,504 B |
| bootloader SHA-256 | `a51852bb09d20fca601dec42cdd4b0afe611e53e87b90778284e6934cece5795` | `1b21ce721cae5f456511e4611d0a2fb61b9809108f39eb488d4eea314c0d0485` |

与 E1.37 32 MiB 参考相比，新候选 App 小 336 B、bootloader 小 736 B，共享字节范围分别有 3,481,266 / 14,825 B 不同。两份显式 Octal 之间，32 MiB App 小 256 B，bootloader 大 480 B。ELF 函数地址/长度变化数为 App 11,170（相对 E1.37）和 6,615（16→32 MiB），bootloader 对应 129/113；多数地址变化可能来自重排，这些数字不表示相同数量的行为修改或行为等价。

## 为什么需要这一轮

E1.38 已重核历史证据：Flash 是 MXIC `C2:8039`、32 MiB Octal，正常运行使用 `mxic (opi)` / `opi_str`。E1.37 的 QIO + auto-detect 构建会在运行时切换接口，但没有开启上半区 mmap 的编译路径；只换 App 的 32 MiB 镜像头也无法解除旧 bootloader 的容量限制。

这一轮在隔离工程中明确选择 Octal，分别构建 16 MiB 控制版和 32 MiB 候选；先保持旧正式分区表。正式 `firmware/body` 产品源码没有修改。构建、浏览器审阅和私有历史文件复核均在电脑上进行，没有串口访问、设备重启或 Flash 写入。设备已验收记录仍为 E1.26 observer-enabled App + E1.18 assets。

## 配置与解释依据

| 项目 | 16 MiB 控制 | 32 MiB 候选 | 含义 |
| --- | --- | --- | --- |
| `ESPTOOLPY_OCT_FLASH` / OPI | 开 | 开 | 显式使用 Octal 编译路径 |
| 自动模式识别 | 关 | 关 | 不把运行时接口识别当作编译配置保证 |
| STR / DTR | 开 / 关 | 开 / 关 | 与已有正常 OPI_STR 证据一致 |
| Flash 频率 | 80 MHz | 80 MHz | 本轮不提升频率 |
| MXIC OPI 驱动 | 开 | 开 | 保留已有芯片族驱动 |
| Octal cache32 | 关 | 开（IDF 派生） | 32 MiB 编译时上半区 guard 放行 |
| Quad cache32 / 实验选项 | 关 / 关 | 关 / 关 | 不使用 Quad 实验路线 |
| `SPI_FLASH_SIZE_OVERRIDE` | 关 | 关 | 容量仍需成对 bootloader/App |
| 正式分区表 | 旧表 | 同一旧表 | 不在容量启动轮迁移资源 |

镜像头的模式字段是 `dout`，它是 IDF 对 OPI 的镜像头表示；物理模式由 Octal 配置、芯片和启动驱动共同决定，不能把这个头字段读成物理 DOUT。

对 E1.37 的 sdkconfig 全量比较：16 MiB 版有 20 项精确差异，32 MiB 版有 24 项；App/bootloader 各自生成的 JSON 差异分别为 16/19 项。额外文本项是 legacy `FLASHMODE_*`，32 MiB 另含 `SPI_FLASH_OCTAL_32BIT_ADDR_ENABLE`。IDF `components/bootloader/sdkconfig.rename:29` 明确将它映射到 `BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH`；它是同一开关的兼容名称，不出现在生成 JSON 中。HPM 和 ROM 实现选项因 `!ESPTOOLPY_OCT_FLASH` 依赖而不可见，不是另外开启的新功能。16→32 MiB 两份显式 Octal 配置之间有 6 项文本差异（5 项容量/cache32 加该兼容别名）。允许清单包含精确键和值，不放行任意差异。

十一处 IDF 5.5.5 源码锚点、摘录及文件摘要保存在 `review.json`，覆盖模式选择、STR、cache32、兼容别名、HPM、ROM 实现、Flash 初始化、mmap guard、32 位地址映射和 MXIC 驱动能力。商业完整料号及独立 SFDP 兼容性证据仍未补齐。

## 可复核输入与产物

私有输入和完成构建留在 `tmp/e139-octal-20261030/`：原调用脚本、输入清单、69 个锁定依赖的内容验证、每版 configure/build 日志、checkpoint、App/bootloader bin/ELF/map、完整 sdkconfig、两份生成配置 JSON、compile commands 和参考资源。原构建调用脚本摘要与当前审阅脚本摘要分别记录，更新审阅不能改写原调用身份。

审计不预设“只改头字段”：完整解析 ESP 镜像段、XOR 校验和和附加 SHA-256，比较各段数据、地址和长度，再比较 ELF 可分配 section 及函数地址/长度。地址变化和 section 摘要只能说明静态代码布局；不能据此承诺运行行为等价。

四项保护产物——旧分区表、源码参考资源、独立语音模型和 OTA 初始数据——要求双构建之间及相对 E1.37 都一致。

**构建资源不是设备活动资源**：`expression_assets.bin` 是原源码参考包 5,889,001 B；当前 E1.18 活动包是 5,538,351 B。独立 model 同样保留构建参考身份，不能冒充完整现场恢复数据。本轮的通用构建 `flash_args` 不是可执行部署计划，不能用于覆盖已验收资源/模型/OTA 数据。

## 回退与运行时边界

按 32 MiB 候选实际 payload 长度，从已验证的 E1.35c **32 MiB 历史全片**切出完整擦除扇区；保存 payload 后末扇区的原始尾部字节。回退文件逐字节核对全片，不使用构建 bootloader、FF 补齐或旧候选范围代替现场数据。全片与回退字节、设备身份和原始日志均不进入公开仓库。

这份历史快照不是实时状态。任何获准写入前都要重新识别端口/芯片、刷新现场备份与保护摘要、确认新候选和擦除范围；本轮不生成可执行刷写脚本。

重算结果：bootloader 回退覆盖 `0x0..0x4000`（16,384 B），App 覆盖 `0x10000..0x456000`（4,481,024 B）。这次恰与旧候选处于相同扇区范围，但结论来自新候选的实际 15,504 / 4,476,944 B 长度重新计算；没有沿用旧范围作为假设。

32 MiB 的 Octal cache32 仅让源码 guard 放行，尚未验证连续 MMU 页、跨界读/mmap、EAF 整区挂载、模型两条加载路径、内部堆/PSRAM 峰值或 LCD 播放。所有部署资格、写入授权、运行验证和硬件验收标志保持 `false`。

## 验证入口

```powershell
python tests/test_storage_octal.py -v
python scripts/build-expression-e1-39-octal.py --check
python scripts/audit-expression-e1-39-octal.py --check
node tests/expression-e1-39-browser.cjs
python scripts/run-offline-checks.py
python scripts/check-publication.py
python scripts/check-workbenches.py
git diff --check
```

前两份 `--check` 复核私有实际字节，要求本机保留输入和 IDF/pyelftools；可移植测试与公开发布检查只验证合成镜像和公开元数据，不假定私有文件随仓库发布。浏览器截图只留项目 `tmp/`。

本轮结果：专项 **29/29 通过**；完整可移植套件 **366 项，364 通过、2 项条件跳过**（仍缺真实最终 F1 导出、本机官方资源能力报告）。保留构建与完整审计 `--check`、1,513 文件发布检查、24 页工作台检查（23 JS / 23 JSON 与 loopback HTTP）、桌面/手机 Edge QA 和 `git diff --check` 均通过。截图已人工查看；手机宽表使用局部横向滚动，展开 JSON 不造成全页横向溢出。主机测试不是启动或硬件验收。

## 下一阶段与验收要求

1. **E1.40：旧布局下成对容量启动**。获得独立写入授权后，先刷新现场身份、备份与精确回退，只允许候选 bootloader + App；旧分区、当前 E1.18 assets、model、NVS 和 C61 保持保护。回读所有写入扇区与保护范围，核对驱动容量 32 MiB、OPI_STR、资源挂载/模型和官方显示、对话中/后切菜单、摸头、睡眠唤醒。底座尚未修复的动作单独验收。
2. **跨界寻址验证**。容量启动稳定后，在单独诊断轮验证 16 MiB 边界上下的实际 read/mmap 字节一致性、映射释放、失败路径和内存峰值；只读验证能否不写测试数据须另审计。
3. **8 MiB 活动资源迁移**。沿用 E1.18 成员和映射，先搬布局、不同时换新动画；保留旧资源/model，验证整区 mmap、内嵌/独立模型回退、掉电恢复和官方交互。活动包暂用 7 MiB 软上限、预留 1 MiB，App 区不缩。
4. **表达内容扩充与客户端**。容量稳定后再逐语义引入动画，审阅对称/非对称、镜像/裁切、局部循环和尾帧；完整母库在电脑。底座修复后完成固定磁铁布局可靠性与登记持久化，再接身体事实、角色确认和受控表达，最后替换为客户端 DeepSeek/TTS。

未来 256 KiB 档案区、第二资源槽和原子切换仍是独立设计，当前没有分配或实现。物理 Flash 容量不能替代运行内存预算或资源更新的一致性协议。
