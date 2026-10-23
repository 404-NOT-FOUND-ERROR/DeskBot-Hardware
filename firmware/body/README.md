# O2f2 body firmware input snapshot

此目录更新为用户已验收的 O2f2 本体工程输入快照，替代旧 R9 研究 UI。上游为 `esp-friends/esp-vocat` @ `46d427255756fe2a148777ea3f256b615b52e160`，目标 ESP-VoCat V1.2 / ESP32-S3，历史构建环境 ESP-IDF 5.5.5。

This is the input snapshot for the accepted local O2f2 App, not an official release or a tested binary built at this public path. No device is flashed by publishing these files.

## 当前能力与边界

- 保留官方 UI、语音、双麦 DOA、摸头、睡眠/唤醒及底座动作所有者。
- 研究模块为固定快照、24 条事件环、XYZ 遥测、RAM 磁场识别与按需只读 MCP。
- 没有旧研究设置页、登记 UI、NVS 外壳档案或新增硬件控制工具。
- 历史 App：4,472,496 B；SHA-256 `4be89c2533909d06e9a8ef43a2aaa67ce9ec1e5a70a762f2827bf4388ac880a5`。
- E1.13 是另外的 assets-only 部署，不改变这里的 App 源码；当前源码内表情仍为原始构建输入，不偷偷替换成部署包。

## 文件与包装差异

| 文件 | 说明 |
| --- | --- |
| `main/`, `partitions/`, `scripts/` | O2f2 应用、分区和构建辅助输入 |
| `components/espressif__touch_sensor_fsm/` | 必要本地触摸组件；含上游预编译输入与许可证 |
| `dependencies.lock` | 固定依赖；本地组件机器路径已改为 `components/espressif__touch_sensor_fsm` |
| `sdkconfig.defaults.o2f2` | 历史有效构建配置输入；不是设备 NVS |
| `UPSTREAM_README*.md`, `UPSTREAM_LICENSE` | 上游说明与来源，不代表此研究快照的状态 |
| `SOURCE_MANIFEST.sha256` | 当前公开快照逐文件 SHA-256，排除自身 |

不包含生成的 `lang_config.h`、`build/`、`managed_components/`、工具链、完整 Flash 或任何设备私密数据。旧 R9 设备页及 Source Han 派生字体已退出当前快照，历史仍在 Git 中。其他上游字体和资源维持原归属。

## 只构建，不刷写

在已初始化 ESP-IDF 5.5.5 的 PowerShell 中，从仓库根目录运行：

```powershell
.\scripts\build-body.ps1
```

脚本执行 reconfigure/build，默认输出 `firmware/body/build/body`，不调用 flash/erase/esptool。首次依赖获取需要 Component Registry 或匹配缓存；完整工程输入不等于当前路径已构建通过。包装后的路径、生成器版本和构建配置会影响二进制，不能将上述历史 App 摘要作为新产物证明。

当前发布验证只执行主机测试与来源/结构/安全检查，没有重新执行整套 ESP-IDF 构建，也没有硬件回归。发布状态见 [baseline-status.md](../../docs/baseline-status.md)，许可证见 [THIRD_PARTY.md](../../THIRD_PARTY.md)。
