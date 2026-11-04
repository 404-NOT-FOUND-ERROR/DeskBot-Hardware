# 当前基线与验证状态 / Baseline Status

2026-11-05 离线开发更新：[E3c-3](deskbot-studio-e3c3-20261105.md)完成状态查询与构建审计；[E3c-4](deskbot-studio-e3c4-20261105.md)增加显示条件查询，212 项专项与回归、双配置构建和独立审计通过。设备仍是下述 E3b 第二版，交互验收待测。历史阶段结果见[更新日志](../CHANGELOG.md)。

2026-11-04：E3b 第二版已安装，App 回读、启动、周期身份、重启和工作台重连检查通过，交互验收待用户确认。E1.42 为已验收恢复版本；[E1.43b-2](expression-continuous-mmap-upper-e1-43b2-20261104.md)连续映射专项留待实测。

## 当前状态（2026-11-04，E3b 第二版安装后）

COM4 当前使用 E3b 第二版 App、E1.40 OPI_STR bootloader、E1.18 资源和 `upper-assets-8m` 分区表。App 为 4,483,424 B，SHA-256 为 `8dc14006255ca30ffce584e81df7aeec2e3fb5dd2721da9136c144fdf312c69e`。本轮写入 `0x10000..0x457000`，App 擦除范围逐字节回读一致，其余范围设备端摘要一致，写后首次启动前 NVS 相同。资源包仍为 5,538,351 B，29 个成员，剩余 2,850,257 B；`surprised → shocked.eaf` 未变。

首次启动与重启各收到 5 条身份消息，设备自报 32 MiB Flash / 16 MiB PSRAM；重启时启动编号变化，工作台重连后身份及序号更新正常。交互验收待用户确认。见[E3b 说明](deskbot-studio-e3b-20261104.md)和[实机报告](../artifacts/deskbot-studio-e3b-20261104/live-deployment.json)。

E1.42 的交互已验收，现场备份和 App 恢复范围已保留。E1.43b 三分钟专项继续待测；运行前按当前 E3b 更新实验基线与恢复计划。原始[E1.43c-3 迁移记录](expression-upper-migration-live-e1-43c3-20261103.md)保留执行当时状态。

## 历史记录

2026-11-03 E1.43a-6 现场检查点：第二次新鲜只读预检通过，COM4 的 App 擦除并集、64 KiB 上半区测试页、全部保护范围及身份均符合固定计划，原 App 已再次正常启动；`flash_written=false`。首次预检读包截断的证据保留，未产生写入。设备仍是 E1.42 App + E1.40 OPI_STR bootloader + E1.18 assets，旧表；实机图样写入及自动恢复尚未执行，本轮人工交互未重测。见[现场预检记录](expression-upper-live-e1-43a6-20261103.md)。

2026-11-02 离线适配器更新：E1.43a-5 现有 18 项适配器/集成测试和 19 项事务控制器测试通过。写入默认关闭；如启用，必须交互确认完整计划 ID，并要求同计划恢复日志与经过哈希校验的持久回退输入。测试使用合成身份与模拟 Flash，没有打开 COM4、复位或写设备；最后已知设备仍是上条记录所述的已验收基线。真实单页 CPU 实验及完整自动恢复尚未执行，见[E1.43a-5 阶段记录](expression-upper-adapter-e1-43a5-20261102.md)。

2026-11-02 最新只读预检：E1.43a-3 已完成 COM4 当前身份核对、32 MiB 全片备份及独立原样校验；E1.42 App、E1.40 bootloader、旧表、E1.18 assets 与模型相符，三段现场精确回退文件校验通过。访问/复位过设备，未执行图样写入、刷 App、改表或 C61 操作；45 秒启动记录包含 LCD/触摸初始化且无 panic/assert。设备已回到正常 App，本轮未重测人工交互。已知内容 CPU 实测和自动恢复仍 pending，见[E1.43a-3 预检记录](expression-upper-backup-e1-43a3-20261102.md)及[机器报告](../artifacts/expression-upper-backup-e1-43a3-20261102/report.json)。下文的“尚待新鲜备份”属于此前阶段状态。

2026-11-02 人工回归补齐：用户明确回复“这些操作都正常”，已验收本次恢复后的屏幕、菜单、摸头、对话及对话中/结束后切菜单。见[限定范围验收记录](../artifacts/expression-capacity-serial-e1-42-3-20261102/restored-physical-regression.json)。下文及旧报告中的本轮人工 pending 保留为该条反馈前的历史状态；不扩展为睡眠/DOA/底座或新资源验收。

2026-11-02 离线阶段更新：E1.43a-2 的固定范围 CPU 只读探针、主机结果校验器、34 项专项测试及默认关闭/启用隔离构建通过；完整范围和数值见[E1.43a-2 结果](expression-upper-pattern-probe-e1-43a2-20261102.md)。没有串口访问、图样写入、固件刷写或分区表变化。CPU 上半区实测仍待新鲜备份和单页精确回退门禁。

本次离线增量：E1.43a-1 的固定非 FF 图样、严格清单/schema 和负向测试已完成；没有连接串口或写设备，基线未因本轮改变。当前执行顺序及防错步骤见[开发主线](development-pipeline-20261102.md)，材料与范围见[E1.43a-1](expression-upper-pattern-e1-43a-20261102.md)。

更新：2026-11-02。当前为此前已验收的 **E1.42 App + E1.40 32 MiB OPI_STR bootloader + E1.18 assets**；E1.26、O2f2 与 E1.13 保留为历史验收锚点，R9 不复用。E1.42.3 临时诊断已真实验证驱动 32 MiB、跨 16 MiB 边界的 CPU read/mmap 和页面释放，随后原 E1.42 App 的 4,485,120 B 擦除扇区已精确恢复且回读一致。本轮恢复后人工交互回归尚待确认。正式分区仍为旧布局，已知非 FF 内容/地址别名、8 MiB 连续 mmap 和资源迁移未验收；MCP 主机证据独立 pending。见[E1.42.3 结果](expression-capacity-serial-e1-42-3-20261102.md)。后文保留各阶段历史状态。

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

## E1.13 assets：已验收 / Accepted

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

写入器校验、完整分区回读逐字节比较、O2f2 App verify、分区表摘要复核通过。用户于 2026-10-24 确认 E1.13 部署后的屏幕、菜单、点击/语音对话、对话中及结束后的菜单切换、摸头、睡眠/唤醒均正常；本体资源交互验收通过。底座维修后的 C61 电机、回零、磁吸滑动、磁场采样和动作事件不包含在本次 E1.13 结论内。

The assets write did not modify the App, bootloader, partition table, OTA data, NVS, speech model or C61. Full readback integrity and the scoped body interaction acceptance passed. C61/base repair validation, client integration and future profile persistence remain separate work. Private backups and raw logs are retained locally and are not published.

## E1.18 assets：当前资源，已验收 / Current Assets, Accepted

E1.18 仅将 `surprised` 从 `Happy.eaf` 映射到已有 `shocked.eaf`，保留其他映射与所有 EAF。资源部署、完整回读和本体回归已通过，用户在 E1.22 对照后选择继续保留该映射。见 [E1.18 验收](e1-18-acceptance-20261024.md)。

## E1.26 App：历史应用，已验收 / Previous App, Accepted

| 项目 / Item | 记录 / Value |
| --- | --- |
| 目标 / Target | COM4 本体 ESP32-S3 |
| 唯一写入 / Only written region | `0x10000`，ota_0 App |
| 有效 App / Image size | 4,477,280 B |
| App SHA-256 | `f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2` |
| App 分区余量 / Remaining App capacity | 1,666,720 B / 6,144,000 B |
| 观察器 / Observer | 显式启用，固定 24 条 RAM 记录，只记录 `SetEmotion` 入口 |

完整 App 回读与候选一致，分区表与 E1.18 assets 只读校验通过。用户于 2026-10-27 回复“一切正常”，本体交互回归记为 `accepted`。底座仍在维修，C61、磁场和整机动作不包含在本次验收内。设备随后通过语音返回了最近 24 条的定性摘要（`happy`、`embarrassed`、`sleepy` 占多数，其余为 `neutral`）；原始调用 JSON、精确次数和顺序尚未取得。调用记录本身也不代表资源加载或 LCD 播放结果。详见 [E1.26 实机记录](expression-e1-26-live-deployment-20261027.md) 和 [E1.27 口述摘要](expression-runtime-observation-e1-27-oral-summary-20261027.md)。

The E1.26 deployment was accepted on 2026-10-27 and remains a historical rollback anchor. The current device runs the E1.40 paired 32 MiB OPI_STR images with unchanged E1.18 assets; see the E1.40 result below.

## 发布等级 / Status Vocabulary

- `offline_validated`：主机输入/输出已验证，不代表设备执行。
- `built`：历史指定输入已构建，不代表当前公开路径已重新构建。
- `deployed_integrity_verified`：写入与回读一致，不等于交互通过。
- `accepted`：对应版本的用户实机验收通过。

当前：E1.40 旧布局成对容量启动及指定本体回归为 `accepted`，E1.18 assets 延续；客户端为 `offline prototype`。CPU 上半区寻址、C61/base 与客户端阶段未标记 accepted。

## E1.36 N32R16 容量审计：离线通过，运行时待验

E1.36 已确认并固定三层事实：物理 Flash 32 MiB、当时镜像/正式旧分区配置 16 MiB、PSRAM 16 MiB。`capacity-only-32m` 和 `upper-assets-8m` 在该历史阶段只是独立候选。六份分区表由 ESP-IDF 5.5.5 生成并反向校验；该报告不具备部署资格。E1.40 已另行部署成对 32 MiB bootloader/App，但正式分区仍旧布局；跨界 mmap、模型双路径、堆/PSRAM 峰值仍待独立测试。

## E1.37 容量对照构建：离线通过，设备未改

[两版构建与审计](expression-capacity-build-e1-37-20261030.md) 已完成。16 MiB 控制 App 精确复现当前 E1.26；32 MiB App 为 4,477,280 B，SHA-256 为 `c863710fec4b66fe3f5354ff8ce8cf21853a7d16bc8d35285aadc1d97568c591`。App 和 bootloader 各仅容量头 1 字节与摘要 32 字节改变，可执行段和 App ELF 相同；分区表、构建资源、模型、OTA 初始数据与正式源码不变。

当前候选没有启用上半区缓存访问，不能用于证明 `upper-assets-8m` 可运行。E1.37 专项 11/11 测试、保留构建/镜像审计、发布检查、22 页工作台检查和桌面/手机 Edge 烟测均通过；完整离线套件为 317 项，315 项通过、2 项条件跳过。下一步先审计具体 Flash/驱动、容量初始化和缓存寻址条件，再准备精确现场回退与容量启动验证。尚未刷写、测量运行内存或进行用户实机验收；当前设备身份与既有验收状态保持上述 E1.26 + E1.18。

## E1.38 寻址与精确回退：离线完成，设备未改

[本轮预检](expression-addressing-e1-38-20261030.md) 重核历史全片和启动证据：Flash 为 MXIC `C2:8039`、32 MiB Octal，运行 OPI_STR；准确商业料号未确认。E1.37 的 QIO 自动识别编译路径未开启两个 cache32 开关；自动切到 OPI 不会解除 mmap 的 24 位 guard。`SIZE_OVERRIDE=false`，只刷 32 MiB App 仍预期受旧 bootloader 的 16 MiB 限制。

现场 bootloader 的编译时间、校验与摘要和构建版不同。E1.38 已按 E1.37 成对候选的实际擦除范围保留精确现场回退：bootloader `0x0..0x4000`，App `0x10000..0x456000`；完整 32 MiB 备份与保护分区摘要继续保留。该快照不是实时状态，写前须刷新现场身份/摘要，新候选须重算范围。

本轮没有串口、重启、新构建或设备写入；E1.26 + E1.18 的验收记录继续有效，不新增硬件验收结论。下一步为 E1.39 隔离显式 Octal 候选与容量预检，之后才单独授权容量启动。结果和验证清单见本轮文档。

E1.38 专项 20/20、完整离线 337 项（335 通过、2 条件跳过）、本机证据审计、公开发布检查、23 页工作台检查、桌面/手机 Edge QA 和 `git diff --check` 均通过。

## E1.39 显式 Octal 双构建：离线通过，设备未改

[构建与预检](expression-octal-e1-39-20261030.md) 完成隔离的 16/32 MiB OPI_STR 构建。App 分别为 4,477,200 / 4,476,944 B，bootloader 15,024 / 15,504 B；32 MiB Octal cache32 按 IDF 派生，Quad/实验路径关闭。完整配置、镜像段、ELF、69 个依赖和现场完整擦除扇区回退已复核，专项测试 29/29 通过。源码参考资源与设备 E1.18 不同，不能执行通用构建 flash_args。

这些产物只具备离线候选身份；当时没有串口访问、重启或刷写，未测真实上半区 mmap、运行内存或 LCD。随后 E1.40 已用独立候选完成成对启动；本节仍是 E1.39 的历史证据，资源迁移、新动画及 C61 继续单独处理。

## E1.40 成对容量启动：已验收 / Accepted

2026-10-30 仅刷 COM4 bootloader `0x0` 和 App `0x10000`。两份镜像实际擦除范围回读一致；分区表、OTA data、PHY、E1.18 assets、语音模型和完整上半区与写前快照一致。刷写命令未触碰 NVS；刷后保护区回读显示 92 个有效条目中，唯一变化项为 `audio.output_volume`（80→100），发生原因和准确时间未知。启动日志确认 MXIC OPI_STR、16 MiB PSRAM、LCD、触摸、资源及模型初始化、联网和对话；用户于 2026-10-31 确认显示/菜单、对话中和结束后切菜单、摸头、睡眠/唤醒全部正常。

当前 App 为 4,476,944 B，SHA-256 `fa57fac6056be8b779a52e97cd1f7410c69d5af4c1798a52560f631ce352e4e5`；bootloader 为 15,504 B，SHA-256 `1b21ce721cae5f456511e4611d0a2fb61b9809108f39eb488d4eea314c0d0485`。正式分区表未改。驱动容量直接查询、CPU 跨 16 MiB 的 read/mmap、资源迁移及维修后底座回归仍是独立闸门。见 [实机记录](expression-capacity-boot-e1-40-live-20261030.md) 和 [机器结果](../artifacts/expression-capacity-boot-e1-40-20261030/live-result.json)。
