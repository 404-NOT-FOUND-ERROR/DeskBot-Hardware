# 摸头联网 · 首个试用

2026-11-20。Wi-Fi 摸头已实机验收：三连触保持小智回应，同一次动作通过 Wi-Fi 进入原生活存档。接收服务重开后补存一次，菜单、对话与睡眠唤醒均正常。

| 环节 | 做法 |
| --- | --- |
| 本体 | 沿用 1% 门槛与原三连触。回调只写入 8 项内存队列，满时保留先到事件。后台等 Wi-Fi 和原系统时钟就绪后按顺序发送，失败退避重试，5 分钟过期，重启清空未发队列 |
| 接口 | 专用 HTTPS `POST /api/hardware/head-touch`。提交 `device_id`、`boot_id`、`firmware_sha256`、`uptime_ms`、`reported_uptime_ms`；保存或确认原回执后返回 204 |
| 绑定与存档 | 加密连接校验证书；试用凭证只用于摸头。按设备、启动、固件与手势发生时间防重。接收端用两次运行时间差估算发生时间，标记为 Wi-Fi 来源，沿用原输入、感知与记忆事务 |

原 USB 和语音分享继续使用本机入口。试用 HTTPS 和原接收服务在同一客户端进程，共用存档。Wi-Fi 试用版与 USB 版分别登记固件摘要，同一手势只走一条入档通道。

本轮安装 **E3f-9d.5-touch-wifi＋双角色包**。App 回读、其余 Flash、NVS 与启动身份通过；USB 转发已停用。两组真实三连触各保存一条 Wi-Fi 记录和一条身体感知事务，原 5 条 USB 记录保留。其中第二组发生于接收服务暂停期间，服务恢复后自动补存；主人确认两组回应、菜单往返 5 次、对话中及结束后切菜单、睡眠唤醒全部正常。记录见 `tmp/body-touch-wifi-20261120/live-hotspot1/physical-acceptance.json`，回退见 `tmp/e3c-live-20261120-wifi-touch-live1/`。

验证：20 项 Node 测试和 12 项 Python／原生 C++ 测试通过。真实 HTTPS 与隔离 SQLite 覆盖凭证、设备绑定、丢回执、重开防重；发送任务验证顺序、转发失败、队满和过期。ESP32-S3 实验 App 为 4,554,928 B，App 分区余 26%；内部静态数据增加 296 B，新增任务栈预算为 6 KiB。分区表、模型和 bootloader 输出一致。归档见 `tmp/body-touch-wifi-20261120/`。

复测与接续：

1. 电脑和桌宠连同一个私人 Wi-Fi。按[环境预检](reproducible-workflow.md)选 ESP-IDF 5.5.5 的 Python，确认 `cryptography` 可导入。用 `scripts/prepare-head-touch-wifi.py --host <电脑私人IPv4> --profile <本机专用目录>` 生成配置，再用 `scripts/build-head-touch-wifi.py --profile <同一目录>` 生成指定网络的 App。当前回环实验包不能刷机联网。实机包必须绑定当前私人 Wi-Fi 地址和证书；地址变化时重新生成。试用配置、凭证和镜像留在本机。
2. 保留原客户端存档与启动参数，增加 `DESKBOT_TOUCH_WIFI_CONFIG=<receiver.json 路径>` 启用专用 HTTPS 入口；使用 Node 24。确认监听地址后，按原 COM4 App 安装流程重新备份和回读，只装 App。回退使用已保存的 E3f-9d.4b-touch App，恢复原客户端启动参数和 USB 同步。
3. 暂停 USB 摸头转发，三连轻触一次，核对小智回应和一条 Wi-Fi 记忆；短暂断开接收端后再触摸，接回服务后应只补存一次。回归菜单、对话与睡眠唤醒。首轮接收服务仍在电脑；下一步迁到独立常驻主机，验证关闭制作电脑后的日常使用。
