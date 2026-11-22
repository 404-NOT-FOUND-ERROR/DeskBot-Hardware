# 4b2b 真实中文语音联测

[设计与当前结果](../../docs/xiaozhi-real-voice-4b2b.md)。实际 WebSocket、Opus 和 Windows 中文识别／发声接入原 Node／SQLite；输入为合成录音，模型为固定回复。

从硬件 Git 根执行，首次准备依赖：

```powershell
python scripts/prepare-voice-runtime-4b2b.py
node scripts/verify-real-voice-4b2b.mjs
```

安装器经 Clash `127.0.0.1:7897` 获取锁定版本，逐文件核对；已有依赖直接验证。本机需要 Windows PowerShell 5.1、中文听写引擎和 `Microsoft Huihui Desktop` 音色。测试创建独立 SQLite，覆盖实际音频、网络、启动关闭、上轮协议与原客户端回归；结果写入 `artifacts/xiaozhi-real-voice-4b2b-20261117/`。

启动电脑端实验服务：

```powershell
node scripts/serve-real-voice-lab-4b2b.mjs --port 8890
```

服务监听 `127.0.0.1`，连接配置保存在 `tmp/real-voice-lab-4b2b/connection.json`，生活库保存在同目录。`--state-dir` 可选择另一个独立目录。窗口内按 Ctrl+C 关闭；自动启动者可经 Node IPC 发送 `{type:'shutdown'}` 完成服务收尾。

下一轮接主人绑定、重开恢复与设备网络迁移，再验收真人收音、声音质量及联网日常交互。
