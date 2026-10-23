# Third-party notices / 第三方来源

本仓库为研究性发布。复制、构建或再分发时，请遵守每个上游项目、组件、字体和资源各自的许可证。根目录 MIT 仅适用于 DeskBot 新增文档、协议与工具，不会重新授权固件快照或动画资产。

| 来源 / Source | 许可与边界 / License and boundary |
| --- | --- |
| ESP-VoCat / XiaoZhi | 固定源码提交 `46d427255756fe2a148777ea3f256b615b52e160`；以上游工程许可及 `firmware/body/UPSTREAM_LICENSE` 为准 |
| ESP-VoCat Base | 底座上游标注 Apache-2.0；完整来源与裁剪边界见 `firmware/base/UPSTREAM.md` |
| ESP-IDF 与组件 | 各组件自身许可证优先；必要本地 `touch_sensor_fsm` 来源与许可证保留在 `firmware/body/components/` |
| 本体表情、图标、语音模型和字体 | 来自上游快照，非 DeskBot 创作；各资源文件/依赖的原始声明继续适用 |
| E1.13 insert/eat | 上游官方动画的无损编码转换；此仓库仅记录包/分区摘要，不发布全片或 NVS。仍按原始资产许可处理 |
| Espressif `esp_emote_gen_player` 示例 | 仅在协议母库元数据与工作台研究中作对照；其播放器和 `.bin` 不属于当前本体兼容格式，不直接包含或刷入 |
| 害羞研究候选的元数据 | 来源为 Espressif `Emote Pack` 中的 `shy` 示例；未找到明确的素材再分发许可，因此公开副本不包含其原始/派生媒体或相关截图，只保留来源、摘要、容量和映射研究。播放器的代码许可证不自动授权素材 |
| 表达链路 SVG | 本项目原创的代码绘制架构图，不是第三方动画或设备截图；MIT |
| Source Han Sans OFL notice/tool | 早期 R9 发布实验的历史许可/字体生成工具；派生 Device 字体不在当前 O2f2 源码树内 |
| DeskBot 新增文件 | MIT，见根目录 `LICENSE` |

不得删除上游文件中的版权头或许可证。公开快照的依赖锁将 `touch_sensor_fsm` 机器绝对路径规范化为仓库相对路径；没有改写上游许可证，也不改变二进制组件内容。
