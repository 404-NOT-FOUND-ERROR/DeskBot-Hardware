# DeskBot Gateway

这是服务器端的第一版文本网关骨架。它的作用是把模型供应商 API 隔离在设备端之外，为后续 ASR、TTS、角色记忆和实验日志提供稳定入口。

## 运行

PowerShell：

```powershell
$env:DESKBOT_DEEPSEEK_API_KEY = "你的 DeepSeek API Key"
python gateway/deskbot_gateway.py
```

健康检查：

```text
GET http://127.0.0.1:8787/health
```

文本接口：

```text
POST http://127.0.0.1:8787/v1/chat/completions
```

请求体使用 OpenAI Chat Completions 结构。网关会把请求转发到 DeepSeek，但不会把 API Key暴露给设备。

## 当前不负责的内容

- 麦克风音频上传
- ASR
- TTS和音色生成
- ESP-Brookesia Realtime/WebRTC适配

这些内容要在文本链路验证后分别加入，避免把设备端、模型端和语音端的问题混在一起。
