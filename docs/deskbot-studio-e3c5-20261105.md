# E3c-5 · 一个平静表情与执行回执

电脑可通过现有 USB 会话提交一个 `neutral` 请求，由原应用主循环调用现有播放器。42 项新增专项与前序回归合计 254 项通过；完整离线回归 1137 项，其中 2 项条件跳过，其余通过。本轮完成离线开发，设备沿用 E3b 第二版。

```mermaid
flowchart LR
    A[电脑提交平静表情] --> B[USB 核对会话、目录和有效期]
    B --> C[已受理 accepted]
    C --> D[原 Application::Schedule]
    D --> E[复核应用、页面和有效期]
    E --> F[原 SetEmotion neutral]
    F --> G[已提交播放器 dispatched]
```

## 请求如何执行

| 情况 | 结果 |
| --- | --- |
| 应用空闲、主页稳定、播放器已初始化 | 受理一个 `neutral` 请求 |
| 正在对话、切页、停在其他页面或已有请求 | 返回忙碌及原因 |
| 排队后会话关闭、状态变化或请求过期 | 取消排队请求并返回原因 |
| 同一请求编号与内容再次发送 | 返回已有决定，最多保留 64 条去重记录 |
| 同一编号换了内容，或身份、目录、角色不匹配 | 返回拒绝原因 |
| 请求其他表情或动作 | 返回当前未支持 |

`neutral` 沿用当前目录的 `winking.eaf`，没有绑定底座动作。协议使用 `deskbot.expression_control/v1`，每条请求含设备、启动、会话、递增序号、目录摘要、角色和有效期。有效期从设备收到完整请求开始计时，允许 100–10,000 ms；UTC 字段用于记录。

`accepted` 表示请求已进入队列；`dispatched` 表示已调用播放器 API。实际睡眠状态仍为 `unknown`，本轮不据此推断屏幕是否亮起。圆屏显示及完整交互在设备联调时确认。

## 实现与验证

- USB 线程负责解析、校验和排队；原 `Application::Schedule` 执行表情调用。
- 最多一个待处理请求、一个保留回调和一个终态回执；旧回调被消费后再接下一条。
- 状态存储与输入缓冲有固定上限，沿用原 USB 任务；持锁范围只覆盖请求状态，调用调度器和播放器前释放锁。
- 测试实际编译运行生产解析器、会话入口、控制器与适配器，覆盖正常提交、忙碌、超时、重复、冲突、断开、重连、页面变化及并发快照。
- 原应用、UI、音频和播放器源码本轮保持原样，构建分别核对默认关闭与启用配置。

结果：[专项与回归](../artifacts/deskbot-studio-e3c5-20261105/offline-validation.json) · [双配置构建](../artifacts/deskbot-studio-e3c5-20261105/firmware-build-report.json) · [独立审计](../artifacts/deskbot-studio-e3c5-20261105/firmware-audit.json)。

## 下一步

1. 工作台复用原串口所有者，展示应用、页面和播放器状态。
2. 增加一个“平静”按钮，显示受理、提交、忙碌、超时与断开结果。
3. 完成离线联调后部署 App，验收对话中及结束后切菜单、摸头、睡眠和唤醒。
4. 扩展真实设备表情目录；底座修复后接已有动作与外壳登记，再对接角色客户端、DeepSeek 和语音服务。

## 复现

从 Git 根运行 Offline 预检，再使用预检确认的 Python 执行：

```powershell
python scripts/validate-deskbot-studio-e3c5.py --check
python scripts/audit-deskbot-studio-e3c5.py --check
```

固件构建使用 IDF Python 运行 `scripts/build-deskbot-studio-e3c5-firmware.py`。构建产物和日志在本工作树 `tmp/e3c5-expression-20261105/`，保留供审计复核。本轮没有访问串口或刷写设备。
