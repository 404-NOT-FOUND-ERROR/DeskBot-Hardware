# 测试范围 / Test Scope

## E3d-1 · 2026-11-05

新增 37 项 Python 测试覆盖资源包目录核对、分页、身份与版本、共享资源及草稿隔离；25 项浏览器检查覆盖动画、节点往返、筛选、搜索、窄屏、深色和减少动效。完整离线套件 1,256 项：1,254 通过、2 项条件跳过。入口为 `run-offline-checks.py` 与 `tests/deskbot-studio-e3d-browser.cjs`。工作台目录来自已验证 E1.18 快照；设备实时目录在 E3d-2 接入。见[阶段说明](../docs/deskbot-studio-e3d1-20261105.md)。

## E3c-4 · 2026-11-05

`python scripts/validate-deskbot-studio-e3c4.py --check`：34 项显示条件专项，加会话、状态、快照与恢复回归，共 212 项通过。实际编译运行生产 C++ 和状态适配器；当前设备仍运行 E3b 第二版。

完整可移植回归使用 `python scripts/run-offline-checks.py`。历史 E1.28 审阅使用固定 SHA-256 源码样本；E1.26 全构建校验器使用合成文件测试规则。原报告和历史部署输入校验保留。

## 当前增量（2026-11-04）

[E3c-1 USB 会话](../docs/deskbot-studio-e3c-design-20261104.md)：28 项主机测试实际编译运行固件 C++，覆盖会话、重连、有效期、诊断回执、JSON 校验和输入边界。原准备套件 83 项复测通过，合计 111 项；设备仍运行 E3b 第二版。

2026-11-05 全套复测 993 项：986 通过、2 跳过、5 项历史快照比对未通过。失败名称与 E2c 相同，涉及 E1.26 的 Kconfig/构建报告及 E1.43 的旧源码清单；见[本轮汇总](../artifacts/deskbot-studio-e3c-20261104/offline-suite-review.json)。历史快照测试应对照对应阶段的源码与报告，当前增量另做专项和双配置构建审计。

E3b App 部署新增 19 项离线测试：覆盖身份与原 App 变化、写入/回读/启动失败、精确回退、进程中断恢复、扇区尾部保留及固定写入地址。真实部署分别记录 App 字节回读、其他 Flash 的设备端摘要校验、启动和交互验收。

[E3b 设备身份](../docs/deskbot-studio-e3b-20261104.md)：27 项协议、C++ 编码与快照测试，11 项服务/生成测试、30 项浏览器检查。覆盖分片、重复/乱序、启动与重连、过期、设备身份、页面布局和模拟器隔离。固件默认关闭/启用分开构建；实机验收单独记录。

[E3a 本体只读观察](../docs/deskbot-studio-e3a-20261104.md)：18 项解析/串口环境测试、9 项服务测试与 25 项浏览器检查通过。覆盖分片、去重、有界记录、日志过期、USB 身份变化、重连、双来源区分和原打包流程。真实 COM4 被动观察 24 秒取得内存摘要；设备状态变化与交互另行观察。源码、Flash 和 NVS 未改。

[E2a 工作台](../docs/deskbot-studio-e2a-20261104.md)：8 项 Python 目录/协议测试、17 项 Node 编辑核心测试和 Edge 交互测试，覆盖五页导航、节点预览、角色隔离、撤销、保存、导入导出和窄屏布局。

[E2b 资源发布](../docs/deskbot-studio-e2b-20261104.md)：18 项 Python 草稿/资源包/本机服务测试、8 项 Node 发布报告测试，以及 Edge 的 15 项端到端检查。验证默认 E1.18 精确复现、E1.45 候选摘要、镜像下载、报告校验、草稿失效和 320/390/1440 像素布局。完整 IDF 构建单独运行，不在便携离线套件内。

[E2c 表达模拟](../docs/deskbot-studio-e2c-20261104.md)：22 项 Python、18 项 Node 和 25 项 Edge 浏览器检查通过，另有编辑/发布核心 25 项回归通过。覆盖目录摘要、请求编号、过期、忙碌、断线重连和动作事实，以及公开/本机两版在 320/390/1440 像素下的导航、正圆预览、深色主题、减少动态效果和打包。完整套件 816 项：809 通过、2 跳过、5 项历史源码快照检查未通过，详见[本轮报告](../artifacts/deskbot-studio-e2c-20261104/verification.json)。所有交互都在电脑模拟器中完成。

[E1.45](../docs/expression-e1-45-staging-20261104.md)新增 11 项资源包测试，覆盖单语义修改、共享成员、布局、容量、填充、整片内存副本恢复和报告错误。候选包与 E1.44 投影一致，原 C 播放证据按相同 EAF/播放器摘要复用；设备交互测试待安排。

[E1.44](../docs/expression-e1-44-adaptation-20261104.md)新增 16 项主机测试，覆盖资源尾标记适配、坏输入、图像保留、容量、单语义隔离与播放报告篡改；官方 C 播放器完成 752 次全帧对照和 8 个默认时序场景。Edge 检查公开/本机节点预览、接线、末帧和手机宽度。新增测试已加入离线套件，设备测试另行安排。

[E1.43b-2](../docs/expression-continuous-mmap-upper-e1-43b2-20261104.md)适配迁移后的分区表和共享映射。99 项专项通过，涵盖容量、运行时记录、事务恢复、迁移留存材料和新入口的实际适配器边界。C++ 探针覆盖 22 个场景。两版完整构建与全套结果见阶段记录。

## E1.43c-3 历史结果（2026-11-03）

E1.43c-3 增加了独立硬件计划模式和同轮备份执行入口；模拟设备与实机适配器不能交叉使用。执行中与验收结果见[E1.43c-3 记录](../docs/expression-upper-migration-live-e1-43c3-20261103.md)。报告审计测试另外覆盖完整回读、缺块及静态区域损坏。

E1.43c-1 的控制器、文件模拟和入口共 46 项通过，布局与迁移回归 53 项通过。两次独立进程演练覆盖资源/分区表写入中断、删除候选后的恢复和重复恢复；完整 32 MiB 模拟镜像还原一致。见[C1 记录](../docs/expression-upper-migration-executor-e1-43c1-20261103.md)和[机器报告](../artifacts/expression-upper-migration-executor-e1-43c1-20261103/report.json)。`test_storage_upper_migration_transaction.py` 已加入默认离线套件。

E1.43c-3 的迁移控制与审计专项共 122 项：43 项传输适配、46 项事务恢复、30 项布局预审和 3 项最终回读审计。硬件计划测试使用内存命令回放器；不会访问 COM4。实机执行另存身份、同轮 32 MiB 备份、逐段回读、恢复日志和启动日志。

本轮完整套件 719 项：714 通过、2 跳过、3 项 E1.26 冻结源码/构建报告检查未通过。较早各轮全套结果单独保留。

## A7 历史离线结果

A7 的入口、同轮快照、事务、适配器、运行时、备份与图样共 **133 项专项通过**。完整套件 540 项：535 通过、2 跳过、1 失败及 2 错误，三项均为历史 E1.26 源码/构建报告检查。证据见[离线报告](../artifacts/expression-upper-snapshot-e1-43a7-20261103/offline-report.json)。A7 关闭/启用构建及[独立产物复核](../artifacts/expression-upper-snapshot-e1-43a7-20261103/build-verification.json)通过，覆盖源码清单、符号隔离、保护产物、配置差异及 App 余量；单页实测待做。A6 原版本恢复后的用户交互验收已通过。

`test_storage_upper_snapshot.py` 覆盖同轮快照、设备/计划/目录绑定、独立清单摘要及篡改拒绝；`test_upper_experiment_entrypoint.py` 覆盖 A7 构建与完整源码清单、唯一安全目录及 v3 恢复兼容。恢复测试另覆盖快照、当前候选或构建缺失时沿用已保存材料。两组测试已纳入默认离线套件。

## 分阶段覆盖

E1.42.3 adds bounded local-console record tests (`test_capacity_serial_e1_42_3.py`). Missing, duplicate, truncated, malformed and unavailable records fail closed. The real serial validator reuses capacity/probe validators and checks the E1.40 reference digest. Default-off and enabled builds separately verify task-symbol exclusion/inclusion and protected binaries. Local CPU evidence does not establish MCP host visibility or non-erased upper-resource playback.

E1.43a-1 adds deterministic fixed-range upper-page address-pattern/schema/negative tests (`test_storage_upper_pattern.py`). They cover all 16 sector markers, reproducible SHA-256, malformed/tampered manifests and patterns, wrong intended port/capacity, range and partition overlap, and the historical table lock. `scripts/prepare-expression-e1-43a-pattern.py --check` is an offline artifact verification and requires the retained private E1.40 reference plus local generated master; it never opens serial or writes Flash. The public test is self-contained. This gate does not authorize or perform a device write.

E1.43a-2 adds fixed-range capture parsing and synthetic-result checks (`test_storage_upper_pattern_runtime.py`) plus isolated full ESP-IDF default-off/on builds (`scripts/build-expression-e1-43a-probe.py`). The build report locks source/dependency hashes, checks the disabled image against E1.42, verifies diagnostic symbol isolation and App headroom, and compares bootloader, partition table, assets, models and OTA data. The 34 pattern/runtime tests are portable; the ESP-IDF build requires the pinned local Python 3.11 toolchain and component donor. Neither operation opens a serial port or writes Flash.

E1.43a-3 preflight adds eight portable identity/security, complete-sector geometry, exact rollback and retained-file integrity tests (`test_upper_backup_gate.py`) to the offline runner. Run the file directly or use `python -m unittest discover -s tests -p test_upper_backup_gate.py`; do not assume `tests` is an importable package. `scripts/audit-expression-e1-43a3-backup.py --check` revalidates the retained 32 MiB capture, independent unmodified-image verification, accepted image/partition hashes, 1,061 source hashes, 18 archived build files and three exact rollback slices. This audit needs the private local evidence and fails when it is absent; it never opens serial or writes Flash. A3 passed its read-only preflight; the later [A6 experiment](../docs/expression-upper-live-e1-43a6-20261103.md) completed exact restoration and interaction acceptance while retaining its failed probe result.

The E1.43a-3 run completed 449 portable tests: 444 passed, two skipped, and three existing E1.26 frozen-source/build-report assertions did not pass (one failure, two errors). The 50 focused pattern/runtime/capture/rollback tests passed. The historical report and assertions remain unchanged; current focused success is not a claim that the full suite or hardware experiment passed.

E1.43a-5 extends `test_storage_upper_experiment_esptool.py` with the COM4-only host adapter, interactive plan confirmation, persisted rollback bundle and matching `recovery.json` phase gate. Its integrated simulation runs the transaction controller through pattern/App writes, probe completion, byte-exact restore and normal-boot result without opening a serial port. The historical A5 run passed 18 adapter/integration and 19 controller tests; current A7 totals are in the report above. This is host-side evidence only; it does not authorize or verify a real device write.

从仓库根目录运行。本仓库离线检查固定使用 Python 3.13；先执行 Offline 预检确认解释器与 `jsonschema`，不要用 ESP-IDF Python 3.11 代替：

```powershell
& '.\scripts\preflight-deskbot-toolchain.ps1' -Mode Offline
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' .\scripts\check-publication.py
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' .\scripts\run-offline-checks.py
```

可移植测试明确列在 `scripts/run-offline-checks.py`：UART 帧重组、语义与去重、磁场识别/遥测、身体事实状态机/schema、EAF 编解码/布局、角色 profile、表达绑定决策、E1.15 至 E1.29 的离线表达/工作台检查、存储容量与回退契约、E1.41 容量查询、E1.42 MCP 可见性/主机捕获及 E1.43a 图样、探针、精确回退、事务、主机适配器、同轮快照和执行入口门禁。`tests/expression-e1-27-browser.cjs`、`tests/expression-e1-28-browser.cjs` 和 `tests/expression-e1-29-browser.cjs` 另用 Edge/Playwright 检查本地页面交互，不纳入默认 Python 套件。E1.29 浏览器测试还将合成快照导入 E1.27，检查节点切换、嵌套 text 显示、窄屏及无网络请求。所有离线检查都不连接串口或设备，不证明实机 UI/DOA/触摸/动画行为，也不授予刷写权限。外部录制或本机构建证据不在仓库时，相应测试明确 skip 或审计失败，不伪称完整覆盖。

E1.28 额外验证动画与动作请求的两条独立支路、三个 PNG 图集的实际字节哈希、五个源文件 SHA-256 锚点，以及口述摘要不扩展成逐条记录的边界。圆屏参考合成来自解码图集，不是 LCD 实拍。

## E1.33 新动画导入

```powershell
python scripts/audit-expression-e1-33-import.py --check --output tmp/expression-e1-33-import-20261029-final
python scripts/build-expression-e1-33-import.py --check
python scripts/build-expression-e1-33-import.py --check --local
python tests/test-expression-e1-33-import.py
node tests/expression-e1-33-import-browser.cjs
```

审计检查精确 E1.18 回退、候选 GIF/EAF 哈希、官方 C 播放器 376 组全帧比较、局部循环段和容量算术。公开 HTML 保留元数据而不含未确认授权的候选图像；`--local` 预览存放在被忽略的仓库 `tmp/`。浏览器烟测覆盖两种页面、局部循环切换、窄屏和无网络请求。它不构建固件、不写设备，也不等于实机播放验收。

## E1.34 候选预处理与布局审阅

```powershell
python scripts/audit-expression-e1-34-preprocess.py --check
python scripts/build-expression-e1-34-preprocess.py --check
python scripts/build-expression-e1-34-preprocess.py --check --local
python -m unittest discover -s tests -p test_eaf_preprocess.py -v
python tests/test-expression-e1-34-preprocess.py
node tests/expression-e1-34-preprocess-browser.cjs
```

审计核验 13 种无损重编码、受控裁切、完整 E1.18 容量投影、752 次保留候选官方 C/布局参考帧比较及默认播放尾帧计时证据。Edge 烟测检查公开/本机候选媒体边界、逐帧/范围/FPS/布局控制、窄屏和无网络请求。公开页面不含未确认可再分发的候选图像；动态预览只放在仓库忽略的 `tmp/`。通过不表示视觉接受、映射变更或实机播放，不生成可刷写包。公开 clone 没有私有媒体时仍检查公开页与报告；`--local` 和浏览器双版本烟测需要本机研究图集。

## E1.31 多角色离线草稿工作台

```powershell
python scripts/build-role-expression-workbench-e1-31.py --check
python -m unittest tests/test-role-expression-workbench-e1-31.py -v
node tests/role-expression-workbench-e1-31-browser.cjs
```

专项 Python 测试已加入默认离线套件。浏览器烟测另需 Edge 与 Playwright；可用 `DESKBOT_PLAYWRIGHT_MODULE` 指定 Playwright 模块路径。它在独立无头浏览器中检查角色复制、兼容布局白名单、断开接线、草稿 JSON 往返、撤销/重做、恢复基线、非法导入保留原状态、窄屏布局和无网络请求。临时导出仅写入仓库 `tmp/e1-31-browser-tests/` 并在结束时清理；不访问用户浏览会话或设备。

## 历史集成套件

`test_role_binding_consumer.py`、`test_shell_binding_workflow_e1_6.py`、`test_client_transport_contract_e1_7.py`、`test_client_record_receiver_e1_8.py` 保留原有研究测试。它们会校验原 E1.4c 发布锁，需要固定提交的上游 Git 对象、候选 EAF 和锁中所有原始输入；这些未全部在公开仓库提供，因此**不在干净 clone 的默认测试范围内**。不为使测试变绿而删除哈希验证或把缺失输入改成通过。

旧 R9 C++ 事件测试已移至 `legacy-r9/`，仅为历史参考；当前主机 C++ 磁场测试为 `o2d_magnetic_core_test.cc`，需支持 C++17 的编译器。浏览器 `.cjs` 脚本是既有 QA 入口，需要另行准备 Playwright 与浏览器，不会由默认测试安装或访问用户浏览会话。

The public CI runs the portable scope and current magnetic C++ core. It does not run ESP-IDF, flash hardware, certify a new firmware binary, or turn the E1.26 offline build audit into hardware acceptance. A clean clone may skip evidence-dependent local-build assertions; the formal E1.26 audit fails closed when its locked reports are absent.
# E1.35a storage preflight (2026-10-29)

`test_storage_budget.py` covers unaligned MMU spans, protected partition bounds, overlap/overflow, future reserve arithmetic, incomplete image segments, build checkpoint path/hash validation, and refusal of fabricated live-memory/deployment claims. It does not contact serial ports or run a firmware build. The offline runner includes it.

`scripts/audit-expression-e1-35a-storage.py --check` additionally requires retained local E1.26/E1.18 inputs, IDF 5.5.5, and the four completed isolated builds. It verifies the report/page without rebuilding firmware. Build outputs and exact device rollback evidence remain in ignored `tmp/`; public JSON contains their hashes. Browser checking is `tests/expression-e1-35a-browser.cjs`.

The E1.35a browser smoke checks the static page at desktop and mobile widths, all phase rows, overflow and the non-atomic migration warning. It rejects page errors and network requests; it does not imply browser-based firmware or hardware validation.

## E1.35b storage migration and rollback audit

`test_storage_migration.py` validates the versioned migration manifest/schema, exact old and candidate boundaries, source/member hashes, full-partition 0xFF padding, protected segments, the three-entry write allowlist, and the complete live backup ranges. Negative tests reject altered tables/models, missing rollback ranges, old flash maps, traversal paths, fake deployment authorization and attempts to write App/NVS/bootloader. `python scripts/build-expression-e1-35b-migration.py --check` is read-only and fails if any staged image or generated report differs; the default build writes only under project `tmp/` and the small public report/visualization. Neither command opens serial ports or writes flash.

## E1.36 N32R16 capacity layout gate

`test_storage_n32r16.py` checks the three-layer capacity model (32 MiB physical Flash, 16 MiB configured legacy image, 16 MiB PSRAM), exact preservation of the legacy partition table for `capacity-only-32m`, the upper-half 8 MiB resource candidate, 64 KiB mapping arithmetic, protected partition invariants and report tampering rejection. `python scripts/audit-expression-e1-36-n32r16-layout.py --check` validates the read-only 32 MiB capture, regenerates all six partition tables with ESP-IDF 5.5.5 and verifies their binary round trips. It writes only under the repository `tmp/` and `artifacts/`; it never opens a serial port, writes Flash, changes NVS or touches COM8. Runtime boot, mmap, model loading, memory peaks and hardware regression remain separate gates.

## E1.37 capacity-only builds

`python tests/test_storage_capacity.py -v` validates synthetic ESP images, segment bounds, XOR checksums, appended SHA-256, header-only differences, the exact six capacity/IDF-derived configuration changes, protected outputs and App headroom. Negative tests reject altered UI/configuration/model/table metadata, invented authorization or acceptance, and claims that the upper-half cache is enabled. These tests are in the portable runner and need no hardware or private build files.

`python scripts/build-expression-e1-37-capacity.py --check` requires both retained private build archives and verifies source/component contents, invocation/checkpoint/log hashes and effective configurations. `python scripts/audit-expression-e1-37-capacity.py --check` additionally checks the image payloads and generated report/page. They fail when evidence is missing and never rebuild or access serial ports. `--review-completed` can refresh comparison metadata from completed checkpoints while retaining the hash-verified original builder; it cannot silently change firmware inputs. A 32 MiB header is not evidence that cached resources above 16 MiB are supported.

`node tests/expression-e1-37-browser.cjs` checks the page at desktop/mobile widths, all six config deltas and both image rows, the embedded offline/cache limits, horizontal overflow and absence of page errors/external requests. It saves screenshots only under project `tmp/e137b-qa/`; this is browser QA, not a device test.

## E1.38 addressing and exact rollback

`python tests/test_storage_addressing.py -v` checks the reviewed MXIC Octal/OPI evidence separately from the DIO image header and QIO auto-detection build choice; it rejects App-only capacity inference, upper-half mmap claims, corrupt bounded images, hidden bootloader code changes, overlapping/partial sector ranges, FF-padded fake rollback, unsafe paths and forged deployment state. The report validator keeps the historical capture and E1.37 build metadata offline-only. `python scripts/audit-expression-e1-38-addressing.py --check` consumes the retained private full-device capture, boot log, build archives and IDF source; missing evidence fails and no serial, reset, build or Flash operation is performed. `node tests/expression-e1-38-browser.cjs` checks the self-contained desktop/mobile page, expandable source anchors, capacity/mmap/rollback tables, no overflow, page errors or network requests. These checks do not authorize a capacity boot or upper-half resource migration.

The E1.37 portable run completed 317 tests: 315 passed, two conditional skips (no final real F1 exports and no local official-source capability report). Historical E1.14/E1.15 generators replay a pinned E1.13 capacity snapshot instead of hashing the evolving status document. Three regression checks ensure that living-document changes cannot alter those historical artifacts, snapshot corruption is rejected, and E1.15 remains byte-identical. The retained historical document fingerprint is not re-certified as the hash of either today's status or the snapshot.

The final E1.38 run on 2026-10-30 completed **337 portable tests: 335 passed, the same two conditional skips**. All 20 addressing/rollback tests passed. Retained-evidence `--check`, publication checks (1,503 files), 23 workbench pages (22 JS/22 JSON payloads plus loopback HTTP), desktop/mobile Edge QA and whitespace checks passed. No firmware was built, no device was opened or reset, and no Flash was written.

## E1.39 explicit-Octal builds and review

`python tests/test_storage_octal.py -v` checks explicit OPI/STR/80 MHz configuration for both App and bootloader, the capacity-derived Octal cache32 choice, image segment geometry/checksums/digests, exact artifact and log records, protected outputs, App headroom and full-sector rollback. Negative tests reject automatic/DTR/Quad/experimental paths, omitted ELF/config evidence, mismatched metadata, partial sectors, unsafe paths and invented deployment/runtime/acceptance state. Portable tests use public metadata and synthetic images; they do not compile firmware or verify private capture bytes.

`python scripts/build-expression-e1-39-octal.py --check` re-reads completed private artifacts, source/dependency contents, configs, checkpoints, logs and the historical full-device capture. `python scripts/audit-expression-e1-39-octal.py --check` also compares the entire sdkconfig and both generated JSON configs against E1.37, all image segments, ELF sections/function geometry and eleven IDF source anchors. Both fail when evidence is missing; neither rebuilds or opens serial ports. Actual builds are an independent opt-in script invocation, confined to project `tmp/`.

`node tests/expression-e1-39-browser.cjs` needs Edge/Playwright and checks the public page at desktop/mobile widths, both candidates, all config/image/rollback rows, embedded JSON and offline flags, expandable report, overflow and absence of page errors/external requests. Screenshots remain in ignored `tmp/e139-octal-20261030/qa/`. This is page QA; hardware startup, cross-boundary mmap and LCD acceptance remain separate.

The E1.39 final run completed **366 portable tests: 364 passed, the same two conditional skips**. All 29 explicit-Octal tests passed. Two actual isolated firmware builds, retained-evidence review/`--check`, publication checks (1,513 files), 24 workbench pages (23 JS/23 JSON payloads and loopback HTTP), desktop/mobile Edge QA and whitespace checks passed. No serial port was opened, device reset or Flash write occurred.

## E1.41 read-only capacity query contract

`python tests/test_capacity_query.py -v` tests the normalized response from the existing `self.get_system_info` tool. It requires a non-negative `flash_size` and a valid partition list, rejects duplicate labels, malformed sizes/addresses, 16 MiB driver reports and drift in the accepted legacy partition table, and always reports `mmap_verified=false` and `upper_assets_verified=false`. The test is part of `scripts/run-offline-checks.py`. It does not call a device, modify the firmware, or substitute a synthetic fixture for the still-pending live tool response.

E1.41 also checks `self.get_flash_capacity`, the AI-visible redacted query registered only by the ESP-Vocat body. The full `self.get_system_info` tool remains user-only; the bounded result contains driver capacity and partition labels/addresses/sizes, with explicit false mmap/resource flags. Neither query writes Flash. A live result is still required before the capacity gate can pass.

## E1.42.1 MCP visibility audit

`python tests/test_mcp_visibility_e1_42.py -v` is included in `scripts/run-offline-checks.py`. It statically verifies that the capacity/addressing and existing DeskBot read-only tools use ordinary `AddTool`, checks the default user-only filter and `tools/list` pagination contract, and optionally checks a startup log from the exact firmware build. Run it only after the Offline preflight has confirmed Python 3.13 and `jsonschema`; it uses the operating system temporary directory and does not depend on ignored local logs.

`python scripts/audit-mcp-visibility-e1-42.py` writes a static report under `artifacts/`. This proves source registration only. It cannot prove that a particular MCP host exposes the tools or accepts a call. Host verification requires the complete paginated `tools/list` plus the raw `tools/call` request and response from the same host; missing host access, a spoken summary, or a firmware rebuild must remain `pending` rather than being counted as a pass. This audit does not access serial, build or flash firmware, change permissions, or alter NVS.

## E1.42.2 MCP host capture gate

Run `python -m unittest tests/test_mcp_host_visibility_e1_42.py -v` or use the default offline runner. The validator checks the complete paired `tools/list` cursor chain, same-session request/response IDs, read-only target calls, JSON-RPC errors and MCP `isError=true` responses. It classifies permission, unknown-tool, invalid-parameter and generic RPC failures. It reuses the E1.41 capacity validator and E1.42 probe parser, retaining nested JSON while reporting capture, payload validity and runtime probe outcome as separate facts.

After a real host capture is available, use `scripts/validate-mcp-host-e1-42.py --capture <capture.json> --output <report.json> --probe-output <probe.json>` with all outputs under repository `tmp/`; then pass the exported probe file to `scripts/validate-expression-e1-42-probe.py`. The test and report tooling is offline-only and does not establish live host access. E1.42 remains pending until complete pagination, same-host calls, capacity validation and the probe's E1.40 reference comparison all pass.

## E1.42.3 local read-only runtime capture

`test_capacity_serial_e1_42_3.py` covers bounded serial records, partial/duplicate captures, malformed/error payloads and the default-off task boundary. The diagnostic task accepts online Idle or a Wi-Fi configuration state stable for ten seconds, then requires three observations of empty audio queues. Speaking/listening/upgrading states are not eligible; the task performs no UI operation or Flash/NVS write and exits once or times out after 120 seconds. These source/format checks do not replace a real device capture.

Run `scripts/build-expression-e1-42-3-serial.py --run-label <unique-label>` with the pinned IDF Python to make a fresh off/on build audit while reusing this stage's isolated cache. The label preserves earlier logs and reports; an existing report or log is rejected. The off variant must omit the task symbol, the on variant must include it, and both retain the accepted bootloader/table/resources/model binaries. Use Offline Python for `scripts/validate-expression-e1-42-3-serial.py <boot.log> --output <new-report.json>` after an actual capture. Device CPU execution can be verified by this local transport independently of pending MCP host visibility; all-FF samples still cannot rule out address aliasing.

## E1.43b 连续映射

2026-11-04：当前用 `upper-assets-8m` 分区表运行，C++ 主机测试覆盖 22 个场景，包括共享已有资源映射。`test_storage_continuous_mmap_baseline.py` 检查迁移回读、分区表、App、模型和当前准备入口；使用保存的私有回读进行文件集成测试，缺少私有材料时跳过相应项。部署准备使用 `prepare-expression-e1-43b-upper-deployment.py`，执行/恢复使用 `run-expression-continuous-mmap-upper-e1-43b.py`。原迁移前运行器按原摘要保留。

使用 Offline 预检确认的 Python 运行 `-m unittest discover -s tests -p test_storage_continuous_mmap.py -v`。测试检查同轮内容参考、记录完整性、8 MiB/128 页、三分钟持有、内存阈值和释放；同时通过 Zig 编译实际探针，模拟 21 种成功与退出场景。Flash、RTOS 和哈希 API 在 C++ 主机测试中均为模拟；SHA-256 内容比对由 Python 独立覆盖。

IDF Python 运行 `scripts/build-expression-e1-43b-probe.py --run-label <唯一名称>`，随后用 `scripts/audit-expression-e1-43b-build.py` 独立复核归档镜像。实测日志由 `scripts/validate-expression-e1-43b-probe.py` 对照同轮 32 MiB 备份和固定摘要校验；UI 交互由用户另行验收。

`test_storage_continuous_mmap_experiment.py` 的 16 项测试通过模拟设备检查 App 擦除范围、持久恢复记录、错误后恢复、静态区域、NVS 变化和恢复入口。恢复测试删除候选文件，并禁止加载构建模块。实际执行/恢复使用上述 `upper` 运行器、IDF Python，只在实机轮次运行。

## E1.43c 上半区迁移预审

`python -m unittest discover -s tests -p test_storage_upper_migration_preflight.py -v` 包含 20 项契约测试和 10 项留存文件集成测试。集成测试需要本地 E1.18 包、填充镜像与 E1.36 表；缺少这些输入时明确跳过。当前本机 30 项全部通过。

覆盖上半区地址/容量、旧资源和模型保留、表提交顺序、包与成员变化、FF 填充、恢复顺序及实机待测状态。`scripts/audit-expression-e1-43c-upper-preflight.py --check` 使用标准库，重核当前固件清单、封存报告、检查点和资源输入；不会打开串口、构建固件或生成分区镜像。恢复执行器和实机迁移另行验收。

## E3c 离线接收、分帧与回退

在 Git 根目录使用 Offline 预检确认的 Python 运行 `scripts/validate-deskbot-studio-e3c-offline.py`，加 `--check` 可核对已保存报告。35 项测试覆盖分片/粘包、2 KiB 边界、错误恢复、重复键、单请求槽、有效期、忙碌、重连、重启和去重上限。该测试已纳入 `scripts/run-offline-checks.py`。

参考模型只支持 `neutral → winking.eaf`，接收与所有者提交分开，回执来源为 `simulator`。结果保存在 `artifacts/deskbot-studio-e3c-20261104/offline-validation.json`；USB 固件接入和屏幕效果按 E3c 设计继续验证。

准备套件共 83 项：请求模型 35、C++ 原生分帧 12、App 部署事务 19、独立回退 17。分帧测试实际编译运行 C++，与 Python 结果比较；恢复测试使用模拟 Flash，覆盖候选缺失、中断、重复恢复和独立结果文件。早期 `preparation-validation.json` 保留原结果，当前复测写入 `preparation-validation-e3c1.json`，同时记录固件中的规范分帧头文件。

```powershell
python scripts/validate-deskbot-studio-e3c-preparation.py --check
python scripts/check-deskbot-studio-recovery.py --check
```

第二条额外读取本机保存的 E3b 备份，核对 E1.42 原 App 与已验收摘要；新 clone 需先取得对应的本机恢复材料。两条均不打开串口。实际设备恢复使用固定 IDF Python 和串口环境。

## 浏览器测试环境

浏览器测试通过 `DESKBOT_PLAYWRIGHT_MODULE` 选择 Playwright 模块，通过 `DESKBOT_BROWSER_EXE` 选择浏览器。模块未指定时使用项目可解析的 `playwright`；本机路径由环境预检确认。浏览器测试单独运行，公开离线总入口包含协议和逻辑测试。

## E3c-1 USB 诊断与会话

```powershell
python scripts/validate-deskbot-studio-e3c-session.py --check
```

28 项测试覆盖 open/ping/close、30 秒有效期、nonce 轮换、断开后的立即重连、错误目标/启动号、250 ms 回复间隔、2 秒残帧超时、2 KiB 边界、JSON 转义与重复键、随机输入恢复、协议结构和 64 位运行时间。诊断仅返回会话结果。测试已加入离线总入口，报告为 `session-validation.json`。

固件构建使用 Build 预检确认的 IDF Python；默认关闭与启用分开构建：

```powershell
& 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' scripts/build-deskbot-studio-e3c-firmware.py --check
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' scripts/audit-deskbot-studio-e3c.py --check
```

2026-11-05 双配置构建与审计通过。关闭版 App 为 4,483,424 B，代码及数据与 E3b 相同；启用版为 4,487,424 B，App 分区剩余 1,656,576 B。六文件差异、有效配置、保护产物与符号核对通过。构建及审计结果分别保存到 E3c 的 `firmware-build-report.json` 和 `firmware-audit.json`。实机收发与交互另行验收。

## E3c-2 应用与页面快照

使用 Offline Python 运行 `scripts/validate-deskbot-studio-e3c2.py --check`。21 项专项实际编译运行生产 C++ 快照核心与适配层，覆盖未知状态、完整切换、重叠及反序完成、异常结束、版本边界、RAII、固定内存和并发读写；加上前序回归共 132 项通过。测试同时比较移除新增配置块后的原应用与页面逻辑，确认原语句保持一致。

固件构建入口为 `scripts/build-deskbot-studio-e3c2-firmware.py`，使用 IDF Python；独立审计入口为 `scripts/audit-deskbot-studio-e3c2.py`，使用 Offline Python。报告保存在 `artifacts/deskbot-studio-e3c2-20261105/`。本轮只生成固件内部快照，USB 状态查询和表情执行在下一增量接入。设备交互仍按 E3b 单独验收。

2026-11-05 两版构建、复核与独立审计通过：关闭版 4,487,424 B，代码和数据与 E3c-1 相同；启用版 4,488,144 B，增加 720 B，分区剩余 1,655,856 B。快照存储实际为 56 B，保护产物一致，默认关闭配置不包含快照符号。本轮未连接设备。

## E3c-3 USB 只读状态查询

`scripts/validate-deskbot-studio-e3c3.py --check` 使用 Offline Python，运行 46 项查询专项和 132 项前序回归。原生 C++ 测试实际运行会话与状态格式化代码，检查会话校验后才读取快照，并将回复交给电脑端与 JSON Schema 校验；电脑端覆盖时效、重连、重复/乱序、身份变化和旧状态清理。当前 178 项通过。

构建入口为 `scripts/build-deskbot-studio-e3c3-firmware.py`，使用 IDF Python；审计入口为 `scripts/audit-deskbot-studio-e3c3.py`，使用 Offline Python。报告位于 `artifacts/deskbot-studio-e3c3-20261105/`。工作台串口联调和设备交互按下一增量安排。

2026-11-05 两版构建、复核与独立审计通过：关闭版 4,488,144 B，代码和数据与 E3c-2 相同；启用版 4,489,152 B，增加 1,008 B，分区剩余 1,654,848 B。快照存储保持 56 B；查询读取函数与回复标记仅在启用版链接，保护产物一致。本轮未访问设备。
