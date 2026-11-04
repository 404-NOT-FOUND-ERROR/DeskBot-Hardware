# E1.43c-2：COM4 传输层

更新：2026-11-03。离线实现和测试通过。未连接 COM4，也未访问设备。

## 本轮内容

[E1.43c-1 控制器](expression-upper-migration-executor-e1-43c1-20261103.md)继续持有写入顺序和恢复状态；本轮增加 COM4/esptool 适配层，复用现有的身份核对、1 MiB 分块读取、定向截断重试和 USB 启动日志采集。

新适配层只允许读取完整快照、计划中静态区、原资源和分区表扇区。写入与持久化日志中的阶段、地址、长度和原始字节一致；范围为新资源 8 MiB 和分区表扇区 4 KiB。每次传输核对 COM4 和设备身份，保留 esptool 校验输出。

启动检查采用已验收 A7 日志中的 App、屏幕、触摸、PSRAM 和 OPI 标记，并检查重复启动、异常及资源/模型错误。当前 `CONFIG_USE_EMOTE_MESSAGE_STYLE=y` 走 Emote 资源路径；分区大小和校验耗时属于另一条 LVGL 路径，已从必需日志中移除。分区和资源内容由回读核对，资源播放与模型功能留到实机验收。

35 项传输测试和 99 项迁移/恢复回归通过。集成测试使用留存 E1.18 包和 E1.36 分区表，模拟写入、删除候选及恢复原资源。全套 708 项：703 通过、2 跳过，3 项历史 E1.26 源码/构建检查未通过。结果和历史失败记录见[机器报告](../artifacts/expression-upper-migration-serial-e1-43c2-20261103/report.json)。

当前 C1 控制器只接受模拟设备，仍会在读取设备身份前拒绝本适配器。尚需在 E1.43b 实机通过后，单独审查并实现受控的实机执行入口。

## 复核

从 Git 工作树运行，使用 Offline 预检确认的 Python 3.13：

```powershell
& '.\scripts\preflight-deskbot-toolchain.ps1' -Mode Offline
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' -m unittest discover -s tests -p test_storage_upper_migration_esptool.py -v
```

IDF Python 3.11 和 `esptool` 4.12.0 已本地核验。模拟执行以假 runner 返回命令输出，不调用该运行时、不打开端口；生产调用仍须按[主线步骤](development-pipeline-20261102.md)重新核验工具链、设备身份和新鲜备份。

## 下一步

先等用户方便时完成 E1.43b 三分钟连续映射与交互验收。通过后，审阅实机执行入口，再核对 32 MiB 同轮备份，迁移 E1.18 并验证资源挂载、屏幕、菜单、摸头和对话。
