# 桌宠联网语音试用

2026-11-19。小智云是日常语音基线。试用沿用 E3f-9d.3，经配网页切换服务地址。桌宠收音，电脑用 Windows 中文听写和发声，DeepSeek Flash 生成回答。回答存入独立试用存档，并发回圆屏和扬声器。

| 环节 | 接口与做法 |
| --- | --- |
| 接入 | 工具先保存原地址，再经配网页的 `/advanced/submit` 只写 `ota_url`，读回后可重启。桌宠启动后校验身份和固件，取回 WebSocket v1 配置 |
| 对话 | 桌宠以 16 kHz 单声道、60 ms Opus 收音。电脑上的文字和语音共用试用存档；聊天页分别显示配置下发、语音连接和回答发出 |
| 退出 | 回到同一配网页，恢复原地址并重启。原地址为空时使用默认小智云，再测一句小智对话 |

依赖：已验收的 E3f-9d.3、同一个私人 Wi-Fi 或手机热点、Node 24、[固定的中文语音依赖](../integrations/client-4b2b/README.md)、客户端快照和备份。小智 MQTT 和试用 WebSocket 取决于启动时的配置响应。试用端口只绑定指定网卡，只向登记的桌宠下发语音配置。聊天页、存档和模型配置留在电脑本机。

真机曾取到试用配置，字幕和声音完整。主人反馈听写准确率、声音和回答自然度均低于小智，记录中也有明显错听。试用结论为**链路接通，体验未通过**。2026-11-19 已通过配网页恢复原小智地址，读回一致并重启；主人确认语音正常，近况工具实际调用返回 `empty`。当前使用小智云，试用存档与原地址备份保留。[准备记录](../artifacts/device-voice-trial-20261119/report.json) · [恢复与实机结果](../artifacts/xiaozhi-cloud-life-bridge-20261119/hardware-acceptance.json)

1. 电脑与桌宠连同一个私人热点。保持 8780 工作台连接头部。运行下方预检和启动命令，打开 [试用聊天页](http://127.0.0.1:4316/conversation)。设备语音端口为 8891。
2. 关闭桌宠已保存的可用 Wi-Fi／热点，再重开桌宠。看到“扫描 Wi-Fi”时轻点圆屏正中央一次，等配网提示出现。然后打开原热点，让电脑先核对试用 IP，再临时连接圆屏显示的 `Xiaozhi-XXXX`，运行 `apply` 保存并读回地址。电脑连回原热点，桌宠重启后检查是否收到配置。
3. 在聊天页用文字留一个新偏好，再用桌宠问起，然后用文字追问。看看字幕和扬声器是否一致；补测唤醒、打断、重连、菜单、摸头和睡眠。状态“语音已发出”表示电脑完成发送，圆屏和扬声器由现场确认。
4. 同第 2 步进入配网，运行 `restore`。打开原 Wi-Fi 后再重启桌宠，完成一次小智对话后关闭电脑端试用。工具从本地记录恢复原地址并读回核对。

```powershell
./scripts/start-device-voice-trial.ps1 -Check -LlmConfig $llmConfig
./scripts/start-device-voice-trial.ps1 -TrustedWifiAddress $pcWifiIp -LlmConfig $llmConfig
node scripts/configure-device-voice-trial.mjs --action apply --ssid $deskbotSetupSsid
node scripts/configure-device-voice-trial.mjs --action restore --ssid $deskbotSetupSsid
```

恢复电脑进程时用同一个电脑 IP 和 `-Resume`，沿用这次试用的存档和配置。服务地址变化时先恢复小智云，再通过启动器 `-StateDirectory` 和地址工具 `--state-dir` 指向同一个新试用目录。

本轮发现自动配网在约 60 秒后启动热点时重启，串口回溯指向旧 Wi-Fi 接口的清理路径。点圆屏进入手动配网已实测可用。热点名称要逐字核对，本轮 `Galaxy S24+` 与 `Galaxy S24` 是两个网络。固件侧的自动配网修复单独验证。

下一轮用小智做实机对照。听写用同一组真人句子，覆盖人名、短句、停顿和轻微背景声，检查关键意思、漏字和首尾。声音用同一段文字盲听，比较停顿、语气和首句等待。回答先喂正确文字，检查它能否用短口语接住当下，并在合适时带出自己的选择或一条有依据的近况。听不清时请主人重说，避免把猜测写成偏好。三项都通过后，再验收整条语音体验。
