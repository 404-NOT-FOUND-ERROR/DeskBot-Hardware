# E3c-3a 实机对照操作单

更新：2026-11-07。查询关闭版与开启版的基础屏幕交互均已验收。当前设备运行查询开启版；App、NVS、非 App 区域和启动核验通过，查询关闭版回退材料保留在本轮事务目录。

## 实测结果

| 检查 | 查询开启版结果 |
| --- | --- |
| 静置 60 秒、菜单往返 5 次 | 用户确认正常 |
| 摸头 3 次、睡眠再唤醒 | 用户确认正常 |
| 对话期间切菜单 | 当时设备离线，未测 |
| USB 会话 `open` | 完整发送 280 B；收到同 request ID 的有效 `session_opened` 回复 |
| USB `state` 查询 | 完整发送 321 B；收到同会话的有效回复，显示 `application=idle`、`page=home` |
| 底座维修后采集 | COM8 记录滑块上移、下移；COM4 收到对应 UART 事件及 706 条 BMM150 XYZ 样本、144 条心跳；本次无复位或崩溃 |

原始请求与回复见[USB 记录](../artifacts/ui-menu-regression-20261106/e3c3a-state-reprobe-20261106.json)。维修后首段静置监听没有底座数据，后续动作窗口收到持续数据。上/下滑块消息分别为 `cmd=03 00 02` 与 `cmd=03 00 01`；本轮没有 `REMOVE_FROM_*` 或 `PLACE_FROM_*` 消息，外壳身份也未分类。摘要、解码样本和原始日志见采集摘要（本机留存：`tmp/base-repair-validation-20261107/magnet-slide-03-summary.json`）、XYZ 样本（本机留存：`tmp/base-repair-validation-20261107/magnet-slide-03-samples.csv`）、COM4 日志（本机留存：`tmp/base-repair-validation-20261107/magnet-slide-03-body.log`）和COM8 日志（本机留存：`tmp/base-repair-validation-20261107/magnet-slide-03-base/session.log`）。

## 底座维修后采集

滑块上移和下移都在 COM8 底座日志与 COM4 本体日志中对应出现，说明这两路 UART 链路已恢复。706 条 `cmd=06` 帧包含 BMM150 XYZ 样本，可用于离线比较磁场变化；它们还没有绑定到带标签的外壳档案。取下/放回虽已操作，但没有单独标时，需用状态标记工具重采；稳定位置与重复安装样本也需继续采集。

COM8 共记录 6 次 `90°`/`105°`目标。每次目标前 15–24 ms，COM4 都记录了同角度的 `AudioAnalysis: Estimated direction`。源码路径为 `DOA_FOLLOW` → `DoaTrackerResultCallback` → `vocat_base_control_set_angle`；滑块上移、下移事件会调用 `ToggleChatState`。用户确认操作期间只有磁铁移动，没有语音、触摸或主动转头，因此这些角度来自声源跟随链路，而不是外壳识别事件；具体触发 DOA 的声音未识别。

随后另有 9 分 48 秒的无状态标记只读记录：COM4 收到 5,824 条 `cmd=06` 磁场样本和 1,174 条心跳，没有滑块事件或复位；COM8 本次没有输出行。记录见采集摘要（本机留存：`tmp/magnetic-state-capture/20261107-060905/summary.json`）和COM4 原始日志（本机留存：`tmp/magnetic-state-capture/20261107-060905/body.log`）。该段没有位置标记，不用于身份分类。

### 首轮带位置标记采集

之后的新窗口按顺序标记基线、下移、上移、取下、装回上移位置。COM4 收到 5,135 条 BMM150 样本，所有窗口超过 10 秒且无串口错误；COM8 没有输出。每段 XYZ 的窗口内波动 RMS 约 5.2–5.5 个计数；上移和下移中心相距 246.0，装回上移位置与上移中心相距 4.0。首轮数据显示位置间读数有差异，装回结果接近原上移位置。

每种状态只采了一次，重复安装稳定性仍待验证；这些距离不是识别置信度。完整结果见[带标记采集报告](../artifacts/magnetic-state-capture-20261107/report.json)，原始数据见 `20261107-live-marked-02`（本机留存：`tmp/magnetic-state-capture/20261107-live-marked-02`）；拆分和比较端到端回归 16 项通过。

本轮仅被动读取 COM4/COM8，没有发送串口命令或写 Flash。CSV 采样文件来自 COM4 的 `cmd=06` 帧；COM8 诊断脚本的 CSV 只识别 `[diag]` 行，因此判断底座行为以原始 `session.log` 为准。

```mermaid
flowchart LR
    A[核对设备并备份] --> B[查询关闭：已通过]
    B --> C[查询开启：屏幕交互已通过]
    C --> D[open 与 state 已通过]
    D --> E[审计并测试 display 查询]
    E --> F[联网完整交互回归]
    F --> G[平静表情与真实目录]
```

## 本轮执行记录

以下命令是本轮环境与部署入口的记录，已执行完成；不要重复安装。后续先完成源码核对，再决定是否需要新的只读探针或固件版本。

离线材料核对：

```powershell
$DeskBotOffline = & .\scripts\preflight-deskbot-toolchain.ps1 -Mode Offline | ConvertFrom-Json
& $DeskBotOffline.offline_python.path scripts/deploy-deskbot-studio-e3c3a.py check --query-enabled --run e3c-live-20261106-e3c3a-query-on
if ($LASTEXITCODE -ne 0) { throw '离线材料核对未通过' }
```

现场关闭工作台的串口观察后，核对 COM4、生成本轮目录并保存现场备份：

```powershell
$DeskBotFlash = & .\scripts\preflight-deskbot-toolchain.ps1 -Mode Flash -Port COM4 | ConvertFrom-Json
$DeskBotRun = 'e3c-live-' + (Get-Date -Format yyyyMMdd) + '-e3c3a-query-on-' + (Get-Date -Format HHmmss)
& $DeskBotFlash.idf_python.path scripts/deploy-deskbot-studio-e3c3a.py prepare-live --query-enabled --run $DeskBotRun --from-run e3c-live-20261106-e3c3a-query-on
if ($LASTEXITCODE -ne 0) { throw '现场预检未通过' }
& $DeskBotFlash.idf_python.path scripts/deploy-deskbot-studio-e3c3a.py install --query-enabled --run $DeskBotRun
if ($LASTEXITCODE -ne 0) { throw '请读取本轮事务与恢复状态' }
```

部署入口核对私有设备身份、当前 App 和完整 Flash 摘要，保存当下 NVS。查询关闭版 App 擦除范围为 `0x10000..0x458000`，查询开启版为 `0x10000..0x459000`，终点均不含；以各轮计划为准。写入后核对回读与启动，再检查 `USB input ready: /dev/secondary`、App/ELF 身份及运行日志。

通过启动核对后，被动收集操作日志：

```powershell
$DeskBotCapture = Join-Path (Join-Path 'tmp' $DeskBotRun) 'captures/menu-passive.log'
if (Test-Path -LiteralPath $DeskBotCapture) { throw '请保留已有日志，使用新的采集文件名' }
& $DeskBotFlash.idf_python.path tools/capture-body-boot.py --port COM4 --no-reset --duration 600 --output $DeskBotCapture
```

采集期间只读取日志。代理记录每次操作时间；只保留一个串口进程。需要恢复时，先结束采集，再用本轮现场目录执行：

```powershell
& $DeskBotFlash.idf_python.path scripts/deploy-deskbot-studio-e3c3a.py recover --query-enabled --run $DeskBotRun
if ($LASTEXITCODE -ne 0) { throw '请读取本轮恢复日志' }
```

恢复入口使用本轮写前的查询关闭版 App 和事务日志，完成回读与启动核对；随后再确认菜单。原始串口日志、身份和备份保存在本轮 `tmp/`，公开报告记录摘要和测试结果。

## 下一步

`open` 与 `state` 已按同一 request ID/会话完成实机核对。下一步使用 [E3c-4a](studio-display-e3c4a-20261107.md)显示查询单项候选，再验收联网菜单。磁场首轮带标签采集完成；接下来记录至少 10 次同位置拆装，验证装回读数的一致性。

完整交互回归覆盖 20 轮对话/菜单往返、摸头、睡眠唤醒和 30 分钟持续运行。原因与源码差异见[菜单排查](ui-menu-regression-20261106.md)，执行经验见[行动指南](development-pipeline-20261102.md#本轮经验落实)。
