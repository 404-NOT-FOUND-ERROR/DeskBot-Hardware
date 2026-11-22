# 磁场外壳识别 / Magnetic Shell Recognition

当前产品规则见[已确认体验](product-experience-20261116.md)：外壳可设计固定磁布局，实际读数受装配、方向、转动与周边磁体影响。首版将稳定变化与候选展示给主人，由主人确认实际安装的外壳；“我换好外壳了”入口可随时进入同一流程。未识别、候选相似或读数不变时也可完成安装。

主人确认的实物与当次样本分别留存。已登记档案经过独立装回、转动和环境变化复测后，可按主人设置启用自动匹配；歧义、未知或环境变化恢复确认。转动与短暂取下先等待，同次变化合并提示。当前后台自动调度已有离线实现，新的主人安装确认和启用规则待接入。

识别模块沿用 O2f2 的有界 RAM 档案、稳定窗口与只读 MCP 查询；当前设备为已验收的 E3f-9d.3 App + 双角色包，实时基线见[主线](project-mainline.md)。A/B、移除和装回已有历史实验；[S1c 采集项目和 S2a 绑定预演](shell-workbench-s1c-s2a.md)电脑端已完成。正式采样按第二组 B 装回接续，用于磁场候选和可选自动匹配；主人安装确认路径独立开发。

2026-11-12：[S1a 离线评估工具](shell-repeatability-s1a.md)已完成。历史滑块记录中，上移位置独立装回和取下对照各 1 次符合预期；下移位置和未登记布局各有 0 次评估。每组目标 10 次独立采集，完整采样接续双角色验收。[预览](../artifacts/shell-repeatability-s1a-20261112/index.html)汇总每次结果与待补次数；28 项专项和 47 项磁场及采集链回归通过。

[S1b 电脑档案保存](shell-profile-archive-s1b.md)已接通参数、登记数据与来源的保存；独立进程加载后从新采样确认，装回与取下回放一致。档案按传感器和安装基准区分，历史示例保留离线标记。档案验证与角色安装确认分别记录，自动匹配按实测与主人设置接入。

2026-11-07 底座修复后的只读采集确认 COM8 与 COM4 间的滑块上、下事件正常，BMM150 连续提供 706 条样本。用户补充确认期间完成上移、下移、取下、装回，只有磁铁操作。日志标出了上下滑动，但没有给取下和装回单独标时；6 次 90°/105°目标变化发生在这组操作期间，无法逐一对应到某个磁铁动作。离线回放没有登记档案：603 条 `unknown` 仅表示无档案可匹配，96 条窗口不稳定，7 条处于 `warming_up`。本次验证了数据通路，不构成识别验收。

对这 706 条样本另做了 5 秒无标签分箱；可见低波动窗口和多个转场窗口，但磁铁动作没有逐段标时，不能把这些窗口映射到某个外壳状态。摘要、XYZ 均值、波动和相邻窗口距离见无标签窗口报告（本机留存：`tmp/base-repair-validation-20261107/magnet-slide-03-unlabelled-windows.json`）。

2026-11-07 新采集为只读且完整：COM4 收到 13,490 行、5,135 条 BMM150 样本；COM8 没有输出。基线、下移、上移、取下、装回各记录一段，样本数依次为 452、366、567、346、416。各段 XYZ 波动 RMS 约 5.2–5.5 个传输计数。上移与下移中心距离为 246.0；上移与装回上移位置相差 4.0；基线与上移相差 40.1。数据表明本次静止测量可分辨这些位置，装回后接近上移读数。

上述五种状态各有一个静止窗口。下一轮每个固定布局至少采集 10 次独立装回，同时分别采集取下和未登记布局；比较同位置离散度、状态间距与识别结果。相似度使用原 M2 定义。采集报告见[首轮带标记结果](../artifacts/magnetic-state-capture-20261107/report.json)，原始日志和窗口明细保存在 `tmp/magnetic-state-capture/20261107-live-marked-02/`。

现场采集先运行工具预检，再启动双串口监听：

```powershell
$DeskBotCaptureEnv = & .\scripts\preflight-magnetic-capture.ps1 | ConvertFrom-Json
& $DeskBotCaptureEnv.idf_python.path tools/capture_magnetic_states.py --body-port COM4 --base-port COM8
```

这里只检查串口采集所需的 ESP-IDF Python 和 `pyserial`，不会构建固件。先摆好并等待状态稳定，再输入 `start baseline`（或 `slider_up`、`slider_down`、`removed`、`reinstalled`）；静止至少 10 秒后输入 `end`，切换状态前先结束当前窗口。全部完成后输入 `stop`。原始日志、状态时间和摘要写入 `tmp/magnetic-state-capture/`。

采集摘要的 `capture_status` 为 `complete` 才继续拆分；没有完整状态标记、未结束的状态或没有本体磁场帧时会返回代码 2，并保留原始日志。串口读取失败返回代码 1。拆分工具也会核对完成状态、未结束窗口和读取错误。

采集结束后，把启动时显示的目录交给拆分工具：

```powershell
& $DeskBotCaptureEnv.idf_python.path tools/split_magnetic_state_capture.py --capture "<采集目录完整路径>"
```

工具按 `markers.csv` 的起止时间生成各状态的本体日志、底座日志和 `manifest.json`，不改原始日志。窗口少于 8 条磁场样本时标记为未就绪；补采后再做 M2 比较。

拆分完成后运行离线比较器：

```powershell
& $DeskBotCaptureEnv.idf_python.path tools/compare_magnetic_state_capture.py --capture "<采集目录完整路径>"
```

报告汇总每个窗口和状态的 XYZ 均值、波动及状态中心距离，也会列出不同装回窗口之间的变化。它只量化本次标记样本，不自动登记身份或给出置信度；结果保存在 `labeled-states/comparison.json`，原始日志和拆分结果不变。

后续仍由设备报告 `no_data / warming_up / known / unknown / ambiguous / unstable / stale` 等身体事实。用户确认与 `magnet_profile_id → role_profile` 绑定由客户端处理；未知外壳询问、备注与角色演化不放在电机回调里。

`0x06` XYZ 是研究底座遥测扩展，不是已证明官方默认输出。完整阶段与回归要求见 [roadmap.md](roadmap.md)。
