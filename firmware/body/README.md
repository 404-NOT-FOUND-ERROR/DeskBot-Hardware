# DeskBot body firmware snapshot

这是 DeskBot 本体（ESP-VoCat / ESP32-S3）的可复现源码输入快照。它来自本地 **R9 display-rollback 实验候选**，用于后续审计、离线构建和增量开发；它不是已经通过实机回归的官方基线，也不包含设备私密数据。

本目录按完整 ESP-IDF 工程输入快照发布，而非只放本地修改文件：包含上游应用源码、必要资源、本地组件和依赖锁。外部 managed component 缓存、ESP-IDF 工具链和编译生成输出需在使用者本机准备，完整输入不等于已在当前公开路径完成构建或实机验收。公开版本另有设备页字体许可替换，来源见仓库根目录 `THIRD_PARTY.md`；因此这里重新构建的镜像不能与下面记录的本地 R9 App 哈希混为一谈。

公开快照的 `ui_font_Device18.c` 和 `ui_font_Device24.c` 已用开放字体思源黑体重新生成，未更改字体符号、字号、页面逻辑或本地 R9 镜像。因此本目录不声称能复现下述本地 R9 的二进制哈希。字体许可与生成步骤见根目录 [`THIRD_PARTY.md`](../../THIRD_PARTY.md)。

## 来源与状态

- 上游工程：`esp-friends/esp-vocat`
- 上游源码提交：`46d427255756fe2a148777ea3f256b615b52e160`
- 快照来源：本地 R9 隔离源码的 `examples/xiaozhi-esp32` 工程；源码哈希见 `SOURCE_MANIFEST.sha256`。
- R9 App 镜像：4,523,504 bytes，SHA-256 `02FC801F8FA14AA181785717DC36B11E1292C258CD9CB5CA0CD5EE2D4A25852D`
- R9 最近一次写入范围：本体 App `0x10000`；未写入 C61 底座、NVS、分区表、表情资源或语音模型。
- 当前状态：**未验收实验候选**。R9 只把以下四个显示链路文件恢复为官方源码，其余历史增量仍保留：

| 文件 | 与官方 clean 源码 SHA-256 |
| --- | --- |
| `main/boards/esp_vocat/ui_bridge.cc` | `A3CFBAB025C8798B0B45C22A52B66E624E463D5585A2041D4E225900966F4181` |
| `main/boards/esp_vocat/ui_bridge.h` | `27904372807F40A70135196031F0B1ED1AB25B73EA43915301694C190B9F2CC4` |
| `main/display/emote_display.cc` | `8A1DAA3501C3D6E68EFE88546B3820DE9AD93C991D357167352B58F692CA96A4` |
| `main/display/emote_display.h` | `12309AA02B3DD89252E59722B6A4D3B21D7185EFA25E048081BADE536A791B3C` |

R9 与完整官方基线不同：底座事件 journal、磁场遥测/外壳识别等历史增量仍位于 `main/boards/esp_vocat/base_control.*` 等文件中。不要把这里构建出的镜像直接刷入设备；必须先完成源码差异、离线测试、独立构建、分区边界检查、明确端口刷写和实机回归。

## 目录说明

- `main/`：本体应用源码和产品资源；`main/assets/lang_config.h` 不提交，它由构建阶段的 `scripts/gen_lang.py` 生成。
- `components/`：本地 touch sensor FSM 组件；依赖锁中的路径已规范化为 `components/espressif__touch_sensor_fsm`。
- `partitions/`：本体分区表输入。
- `scripts/`：上游构建辅助脚本；本仓库的便携构建入口位于 `../../scripts/build-body.ps1`。
- `sdkconfig.defaults.r9`：R9 有效配置快照，不含 Wi-Fi 密码、API Token 或设备 NVS。
- `UPSTREAM_README*.md`、`UPSTREAM_LICENSE`：上游说明和许可证，仅作来源记录；本文件才是本快照的当前说明。
- `SOURCE_MANIFEST.sha256`：快照文件的源哈希清单，可用于复制后核对完整性。

## 仅构建，不刷写

在已经初始化 ESP-IDF 5.5.x 的 PowerShell 中，从仓库根目录运行：

```powershell
.\scripts\build-body.ps1
```

脚本只执行 `idf.py reconfigure` 和 `idf.py build`，输出到 `firmware/body/build/body`，不会调用 `flash`、`erase` 或 `esptool`。ESP-IDF、组件缓存和编译工具由使用者本地提供；`managed_components/` 和任何 build output 均不在快照中。首次构建需要能访问 ESP-IDF Component Registry，或使用与依赖锁一致的本地组件缓存；不能仅凭本快照的结构检查称为完成构建。

## 许可

本目录中的上游代码、组件、资源和说明继续受各自上游许可证约束。根目录许可证副本见 `LICENSE`，上游许可证来源记录见 `UPSTREAM_LICENSE`；不要将本快照解释为重新授权。
