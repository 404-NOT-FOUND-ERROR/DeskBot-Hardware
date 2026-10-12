# Third-party notices

本仓库发布可复现的研究文档、协议、工具，以及构建所需的上游源码和资产快照。若从 `firmware/` 重新构建，必须同时遵守各上游项目和组件的许可证。

| 来源 | 许可证/说明 |
| --- | --- |
| ESP-VoCat / 小智上游 | 以对应上游仓库中的许可证和提交为准；本仓库记录提交，不重新授权 |
| ESP-VoCat Base | Apache-2.0（底座上游） |
| ESP-IDF 与 Espressif 组件 | 以各组件目录的许可证为准 |
| touch_sensor_fsm、BMM150 等传感器组件 | 保留各自 Apache-2.0、BSD-3-Clause 或上游声明 |
| Source Han Sans 字体位图子集 | SIL Open Font License 1.1，见 `licenses/SourceHanSans-OFL.txt`；生成脚本为 `tools/generate-device-fonts.ps1` |
| 设备字体生成结果 | 仅包含 Source Han Sans 的必要字符子集；其 SHA-256 和生成方法写入生成的 C 文件头 |
| `font_puhui_common_20_4.bin` 与 `78/xiaozhi-fonts` | 上游清单声明 MIT；该二进制由小智资源链路使用，来源和再分发条件仍以其上游仓库为准，本仓库不将其解释为本项目新增字体或独立授权 |
| 本仓库新增文档、协议和工具 | MIT，见 `LICENSE` |

公开发布时不应删除上游源文件中的版权和许可证头。

## 新增设备页字体

`ui_font_Device18.c` 与 `ui_font_Device24.c` 是名为 DeskBot Device 的位图子集，来源为 Source Han Sans SC Normal，字体文件 SHA-256 写在各 C 文件头中。其派生字体仍按 SIL OFL 1.1 发布，不受根目录 MIT 重新授权。原来的本地 Microsoft YaHei 派生位图没有进入公开快照。

普通固件构建直接编译已生成的 C 文件，不需要安装字体。若要重新生成，在 Windows PowerShell 中下载 [Adobe 官方思源黑体](https://github.com/adobe-fonts/source-han-sans) 的 `SourceHanSansSC-Normal.otf`（保留其 OFL 许可），然后执行：

```powershell
.\tools\generate-device-fonts.ps1 -FontFile "C:\Fonts\SourceHanSansSC-Normal.otf"
```

脚本只加载指定的私有字体，拒绝不匹配的字体族，不使用系统字体作为回退。Windows 字体栅格化可能随系统版本变化；生成时应核对字符覆盖和页面排版，公开快照提供的 C 文件才是固定构建输入。
