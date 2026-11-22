# E3c-5b 平静表情部署

目标：在已验收 E3c-4b 上接入电脑发送的单个 `neutral` 表情与回执，继续使用原应用调度、显示驱动和 USB 接收节奏。

## 输入与依赖

候选来自 `studio-stable-ladder-20261108/neutral/on`，App 为 4,510,480 B；构建报告和 7 项候选原生测试均固定摘要。回退版本为已验收 E3c-4b，依据 `e3c4b-live1` 的安装事务和新增人工验收记录。复用现有 App 事务、串口适配器及持久日志。

两版固件按各自冻结源码、构建产物和日志核对。电脑端按本轮原生报告核对当前来源，含 USB 写入 7 秒、请求等待 8 秒与本机确认 10 秒；历史报告保留原内容。

## 接口与数据流

`deploy-e3c5b-neutral.py` 提供 `prepare / check / prepare-live / install / recover / restart`。核验模块提供 `reviewed_candidate / check_build_evidence / load_reference / check_current / prepare_plan / check_plan`，与现有事务接口一致。

已验证 E3c-4b 事务 → 重建完整参考快照 → 离线计划 → 现场刷新前 64 KiB 并核对整片摘要 → 保存当轮计划和回退字节 → 安装与完整回读 → 启动核对 → 日常交互及平静表情验收。

参考快照由上一轮 `snapshot[:0x10000] + candidate + snapshot[0x459000:]` 构成。现场准备保存当前 NVS；其余字节与参考一致。新 App 擦除并集为 `[0x10000, 0x45e000)`，共 4,513,792 B。恢复使用当轮快照及日志，候选与构建目录可独立移除。

## 验证

单元与模拟集成覆盖固定输入、App/ELF 摘要、回退版本、精确并集、NVS 刷新、静态字节变化、重复准备、离线计划状态、安装/故障恢复、候选删除后恢复和路径边界。7 项既有原生测试覆盖接受与调度、菜单/语音切换、超时和重复请求。

准备通过后分别核对菜单、对话、摸头与睡眠唤醒，再发送一次 `neutral` 并核对 `accepted` / `dispatched`。`dispatched` 表示原播放器已收到调用，圆屏效果由实机确认。

## 操作入口

```powershell
& 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' scripts/deploy-e3c5b-neutral.py prepare --run e3c-live-20261108-e3c5b-offline1
& 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' scripts/deploy-e3c5b-neutral.py check --run e3c-live-20261108-e3c5b-offline1
```

结果：7 项候选原生测试、20 项部署单元与模拟集成测试均通过，0 跳过。`offline1` 的实际 `prepare / check` 已通过，计划保存在仓库 `tmp/e3c-live-20261108-e3c5b-offline1/`。报告见[原生测试](../artifacts/studio-neutral-e3c5b-20261108/native-validation.json)和[部署测试](../artifacts/studio-neutral-e3c5b-20261108/deployment-validation.json)。

现场已完成安装：App 回读、其余区域、NVS 与启动核对通过。热点打开后，经生产 HTTP 桥接和串口进程发送一次 `neutral`，得到同编号的 `accepted` / `dispatched`，零拒绝，命令提交到终态约 8.8 秒。最初配网状态的查询与后续成功验证分别归档，见[实机结果](../artifacts/studio-neutral-e3c5b-20261108/live-result.json)。

用户报告一次整体画面花屏，重启后恢复正常。当前运行 E3c-5b，回退版本为已验收 E3c-4b；屏幕观察与日常交互继续核对，见[人工反馈](../artifacts/studio-neutral-e3c5b-20261108/manual-verification.json)及[排查安排](studio-display-observation-20261108.md)。

当前资源仍为 E1.18；“平静”对应原有 `winking.eaf` 眨眼动画，20 fps、单次播放。工作台入口：USB 连接页 → 启用状态查询 → 待机后点击“播放平静”。设备保持正常运行，验收按主人方便的时间进行。

运行环境：候选原生测试用 Offline Python 3.13（含 JSON Schema）；部署用固定 IDF Python 3.11。专项测试入口显式绑定仓库 `tests`，与环境中同名包分别加载。

后续记录：主人确认“就当正常吧，目前没出现过问题，我们继续”，已单独保存[当前使用验收](../artifacts/studio-neutral-e3c5b-20261108/acceptance-20261108.json)。随后安装[E3d-2b 目录版](studio-catalog-e3d2b-20261108.md)，E3c-5b 成为该轮回退版本。上文保留本轮历史结果。
