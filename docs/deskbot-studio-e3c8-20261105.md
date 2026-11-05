# E3c-8 · USB 请求收发

本轮修正工作台与桌宠之间的接收速度。工作台、会话、表情请求继续使用原有链路。

## 原因与改动

IDF 5.5.5 的 basic VFS 在当前配置下，每次读取实际只返回 1 字节。旧循环每读一次等待 10 ms，一条 260 字节请求实测写入约 2.6 秒，超过两秒状态时效。

新循环每轮最多读取 512 次、512 字节，无数据即退出，然后让出 CPU 10 ms。电脑串口写入上限为 5 秒；状态时效仍为 2 秒，表达有效期沿用原协议。

```mermaid
flowchart LR
    A[工作台启用查询] --> B[唯一 USB 连接]
    B --> C[设备按批读取]
    C --> D[应用、页面、播放器状态]
    D --> E[待机主页：播放平静]
    E --> F[原应用调度]
    F --> G[已受理 → 已提交播放器]
    G --> A
```

## 验证

| 项目 | 结果 |
| --- | --- |
| 接收任务 | 9 项生产 C++ 测试通过，覆盖单字节、批量、持续输入、空输入及驱动归属 |
| 接口与恢复 | 336 项专项通过 |
| 完整离线回归 | 1,219 项，1,217 通过、2 项条件跳过 |
| 独立构建 | 关闭、启用两版通过；配置与保护产物一致 |
| 启用版 App | 4,510,480 B，分区剩余 1,633,520 B |
| 实机安装与通信 | COM4 App 回读、保护区/NVS、启动、重启和工作台状态查询通过 |
| 实际收发 | 写入 47 ms、往返 156 ms；旧版相同显示查询为 2,547 ms、3,093 ms |
| 人工交互 | 待测试屏幕、对话、菜单、摸头及睡眠唤醒 |

本轮固件差异集中在 USB 接收任务。表情仍由原应用调度，`neutral` 对应 `winking.eaf`。回执表示请求进入队列及提交播放器 API，圆屏效果由实机交互确认。

设备目前返回“配网中、主页、播放器已初始化”。本轮发送了只读查询，表情请求数为 0；联网回到待机后继续验收平静请求和回执。

## 使用与恢复

打开[本机工作台](http://127.0.0.1:8766/#connection)，连接本体并启用状态查询。应用处于待机、页面为主页、播放器就绪时，“播放平静”可用。

部署入口为 `scripts/deploy-deskbot-studio-e3c8.py`，按 `prepare → install → restart` 执行。写入范围为 COM4 的 App 擦除范围 `0x10000..0x45E000`；当轮快照保存当前 E3c-7，`recover --run <本轮编号>` 可精确恢复。E3b 与已验收 E1.42 的恢复材料继续保留。

状态查询验证用 `tools/verify-studio-control-e3c.py --profile e3c8 --run <本轮编号> --query-only --evidence-name control-evidence-query.json`；相同参数交给 `scripts/audit-deskbot-studio-e3c-live.py` 可复核安装、重启和查询证据。联网待机后，去掉 `--query-only` 验证一次平静请求、去重和新会话。

## 接下来

1. 完成单次平静请求、重复请求与重连验证，补齐人工交互回归。
2. 读取设备真实资源目录，在工作台区分已安装资源和电脑草稿。
3. 开放已安装表情；底座修好后接现有动作和外壳持久登记。
4. 客户端确认外壳与角色，随后接 DeepSeek 与声音。

结果：[构建](../artifacts/deskbot-studio-e3c8-20261105/firmware-build-report.json) · [审计](../artifacts/deskbot-studio-e3c8-20261105/firmware-audit.json) · [专项](../artifacts/deskbot-studio-e3c8-20261105/focused-validation.json) · [完整回归](../artifacts/deskbot-studio-e3c8-20261105/full-offline-validation.json) · [实机查询](../artifacts/deskbot-studio-e3c8-20261105/connection-verification.json) · [耗时对照](../artifacts/deskbot-studio-e3c8-20261105/transport-latency.json)。
