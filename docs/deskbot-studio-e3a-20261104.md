# DeskBot Studio E3a · 本体只读观察

E3a 把本体已有 USB 控制台日志显示在工作台。只读取状态变化和内部内存摘要，不发送设备命令、不改固件。

## 使用

从 Git 根目录启动本机服务。入口先核对离线 Python 与串口 Python，再启动工作台：

```powershell
& .\scripts\start-deskbot-studio-e3a.ps1
```

浏览器打开 [本机工作台](http://127.0.0.1:8766/#connection)，按“刷新端口”、选择本体对应的 Espressif USB 控制台（本次为 COM4），再点“连接只读观察”。需要有 USB 串口驱动且端口空闲。使用“停止观察”关闭读取。

原串口参数在打开前将 DTR/RTS 置低；读取波特率为 115200。端口列表仅显示匹配的 Espressif USB Serial/JTAG，并以序列号摘要确认同一连接目标。服务只监听 `127.0.0.1`。

## 页面显示

| 字段 | 来源与含义 |
| --- | --- |
| USB 串口 | 本机枚举；COM 编号每次可能变化 |
| 最后观察状态 | Application 日志记录的状态变化；不代表当前 UI 页面 |
| 内部可用内存 | SystemInfo 的内部 SRAM 空闲量；不包含 PSRAM |
| 最近日志 | 主机收到符合格式的状态或内存日志的时间 |
| 最近变化 | 本次观察里的状态、串口和启动边界；最多保留 24 条 |

“实时更新”表示 30 秒内收到新数据。重新连接、应用时间回退或 ROM 启动标识会建立新的观察段，不把之前的状态当作本次实况。没有状态变化时显示“尚未观察到”。采集解析后不保存原始控制台行、语音文字、网络凭据或设备 ID。

E1.18 表情目录来自已保存的发布记录；表达请求在电脑模拟器中验证。设备表达入口将在 E3b 设计。

## 本轮验证

- 在线观察 COM4 24 秒，9 次快照含有效日志；内部空闲 SRAM 为 64,507 B，最低记录为 39,812 B。
- 这段采集未遇到状态变化，不能据此判断设备当时在待机、聆听或说话。
- 18 项串口解析/重连测试、9 项服务测试和 25 项浏览器检查通过；E2c/E2b/E2a 共 43 项核心回归通过。
- 浏览器的状态场景使用合成样本；[实机摘要](../artifacts/deskbot-studio-e3a-20261104/live-validation.json)和[检查汇总](../artifacts/deskbot-studio-e3a-20261104/verification.json)分别保存。重建页面只生成构建结果。
- 固件源码、Flash、NVS、底座和资源目录均未更改。

重建公开页和本机页：

```powershell
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' scripts\build-deskbot-studio-e3a.py
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' scripts\build-deskbot-studio-e3a.py --local
```

离线解析与服务测试由 `scripts/run-offline-checks.py` 纳入；浏览器检查为 `node tests/deskbot-studio-e3a-browser.cjs`。完整架构与后续阶段见[开发计划](development-plan-20261104.md)。
