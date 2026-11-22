# 4b1b：客户端到圆屏

2026-11-16。接续已通过的 4b1a，将原客户端的平静／开心输出交给当前 E3f-9d.3 播放器。

## 本轮做法

沿原 `POST /api/chat → outbox → PresentationCoordinator → ControlBridge → USB` 运行，保存对应设备回执。先使用客户端自带固定回复核对真实传输与原生活状态入口；模型内容效果在统一语音阶段比较。

客户端使用独立 SQLite，工作台沿用 8780 与原配置。连接时读取 COM4 实际 hello，核对 ESP32-S3、设备编号和原角色图，再启用状态与角色查询。COM8 保持原状。

接口沿用 4b1a：本机文字输入接收 `event_id / correlation_id / message`，绑定由实际设备状态生成。发送原输入后核对平静／开心命令、USB 请求编号、`dispatched` 及 HTTP 确认；主人观察画面并反馈。

`serve-client-expression-4b1b.mjs` 启动固定基线的独立客户端，使用真实时钟和隔离存档。工作台 `/client-expression` 提供“普通问候／分享开心”和结果显示，沿用同一本机接口。页面明确标注固定回复模式，保留原连接页与菜单测试入口。

## 验证与交付

1. 读取身份、主页和角色状态，启动独立客户端与现有工作台适配器。
2. 主人可观察时，依次用普通问候与开心消息触发两种表情；文字回应与源事件对应。
3. 观察画面完整性、镜像与过渡；操作菜单、对话、摸头、睡眠唤醒。
4. 分别记录自动提交结果和主人验收，再接 4b2 常驻联网与统一语音。

本轮补充依赖路径、固定启动入口与真实 USB 发现的问题，进行针对性测试和独立 AI 复核。

## 当前结果

2026-11-17：自动联调与主人实机验收均通过。当前验收使用圆团角色。

- COM4 的实际 ELF 与已验收 E3f-9d.3 一致；32 MiB Flash、16 MiB PSRAM。当前圆团角色及 37 项目录核对通过。
- 普通问候产生 `neutral`，分享开心产生 `happy`。本轮“平静 → 开心 → 平静 → 开心”四条输入均收到对应 USB `dispatched`，原输出队列确认 `completed / player_dispatched`。
- 2026-11-16 完成的 43 项 Python／真实 HTTP 检查、14 项浏览器检查通过。覆盖真实时钟、隔离 SQLite、原回复接续、页面请求重试、角色时效及窄屏显示；独立 AI 复核通过。
- 设备沿用原固件和资源。客户端本轮使用固定回复；现有小智语音继续运行，统一语音接续 4b2。

### 实机验收

主人确认：四次播放、画面、镜像、过渡及连续按钮使用正常；菜单往返 5 次、对话中与结束后切菜单、摸头 3 次、睡眠再唤醒均正常。本轮沿用原 App 与资源，已核对同一启动会话的四条设备回执。

[交流到圆屏](http://127.0.0.1:8780/client-expression)保留重播入口。验收归档见 `artifacts/client-expression-acceptance-20261117/`。下一步 4b2 先核对设备网络协议与小智兼容接缝，再接真实语音和同一个体的生活状态。

### 启动与接续

客户端入口：`scripts/serve-client-expression-4b1b.mjs --client-root <已应用4b1a的客户端目录> --database <独立SQLite> --port 4314`。启动时同时核对固定提交和已审核补丁的五份文件摘要，使用当前时钟。

工作台沿用 8780、原 `--state-path`，增加 `--client-url http://127.0.0.1:4314`、`--client-device-id <实际hello设备编号>`、`--client-ledger tmp/client-expression-4b1b/ledger.sqlite`。串口解释器固定为 `D:/Espressif/python_env/idf5.5_py3.11_env/Scripts/python.exe`；启动前检查 `import serial`。

启用查询时，`connection_id` 取控制状态，`target_id / boot_id` 取实际 hello，首次 `session_id` 为空。命令编号以字母开头。工作台断线后先重读这些值，再建立新会话。

页面遇到回应丢失时，“接续本次输入”沿用原事件和消息取回原答语。新输入等待上次结果；输入错误、播放器提交和主人观察分别显示。客户端返回非 JSON 错误时保留原 HTTP 状态。

自动联调与测试归档：`artifacts/client-expression-loop-4b1b-20261116/`；本轮实机验收与复核：`artifacts/client-expression-acceptance-20261117/`。原始现场记录保留在 `tmp/client-expression-acceptance-20261117/`。
