# 4b2a 语音协议联测

[设计和范围](../../docs/xiaozhi-voice-seam-4b2a.md)。小智音频封包、回合会话和原客户端 HTTP 接缝，使用 Node 24 内置模块。

从硬件 Git 根执行：

```powershell
node scripts/verify-xiaozhi-voice-seam-4b2a.mjs
```

联测先核对 `tmp/deskbot-client-capability-4a-20261116` 的固定提交和五份 4b1a 文件摘要，再启动临时端口与独立 SQLite。测试音频仅核对数据封包；原生活服务负责保存输入并产生唯一回答。测试队列记 `protocol_fixture`，实际网络和声音进入下一轮。

结果：`artifacts/xiaozhi-voice-seam-4b2a-20261117/report.json`。原始 TAP：`tmp/xiaozhi-voice-seam-4b2a-20261117/validation.tap`。

`recoverUnknown()` 接续结果未知的原转写；`retryCleanup()` 补齐丢失的原队列确认。两者使用本进程内的原请求记录，恢复仅收尾；常驻版本接持久账本和重开恢复。
