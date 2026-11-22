# 统一语音：电脑联调已通过

2026-11-19。桌宠日常仍使用小智云。试验复用小智的通信协议，由 DeskBot 管对话和存档，DeepSeek 生成回答，Windows 提供中文听写和发声。

**数据流**：中文音频 → 小智 WebSocket 与 Opus → Windows 中文听写 → `POST /api/voice/asr` → 原聊天服务与 SQLite → DeepSeek → 同一条回复生成字幕和语音。后续文字通过 `POST /api/chat` 读取同一段交流。

**接口与依赖**：`serve-client-voice-pilot.mjs` 复用已核验的客户端快照、Node 24、`ws`、`opusscript`、Windows 中文语音引擎和本地 DeepSeek 配置。启动时从有摘要的完整 SQLite 备份复制独立存档，两端只监听 `127.0.0.1`。WebSocket 沿用 `/xiaozhi/v1`、令牌和固定设备标识；设备须已在存档登记。`listen.start` 同步冻结当前安装编号；当时没有安装就保留 `null`。

USB 工作台继续使用原存档。试验入口关闭队列读取，只收尾本次语音回合对应的旧设备命令。状态记录音频提交进度。打断停止旧音频，已提交的交流按原编号收尾，重连后继续读取同一存档。普通问答保留最近两轮，沿用原有历史边界和过滤规则。试验版在表情无法匹配时使用平静画面，照常播出回复。

**结果**：文字告诉它“靛蓝色”，合成录音直接问“我最喜欢什么颜色”，再用文字追问。识别、三次真实 DeepSeek 调用、同一回复的存档／字幕／音频均通过。67 项检查覆盖换壳、打断、重连、重开存档、模型失败和按原编号恢复；独立复核通过。见[结果记录](../artifacts/client-voice-pilot-20261119/report.json)。电脑端已收到并解码音频，实机麦克风与扬声器在下一步验证。

**复跑**：先核对 Node 24、[锁定的语音依赖](../integrations/client-4b2b/README.md)、Windows 中文引擎、客户端快照和 SQLite 备份。用启动器传入 `--client-root`、`--backup`、`--backup-sha256`、`--config-path`、`--source-id` 与独立 `--state-dir`。默认端口为文字 4316、语音 8891；重开同一试验存档使用 `--resume`。统一语音专项用 `node --test tests/client-voice-pilot.test.mjs`，设置 `DESKBOT_VOICE_PILOT_CONFIG` 为本机模型配置文件路径即可加入真实联测。丢响应或命令收尾失败时调用本机 `POST /api/voice/pilot/recover`，沿用原编号恢复。

**接续：真机 Wi-Fi**。[可恢复的试用入口](xiaozhi-device-voice-trial.md)已完成电脑联测。E3f-9d.3 沿用 16 kHz 单声道、60 ms Opus，原配网页选择服务，日常继续小智云。共用私人热点后验真人收音、上下文、扬声器、打断、断网重连和切回；沿用菜单、摸头及睡眠回归。
