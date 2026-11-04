# E1.17 官方 C 播放器兼容性审计

日期：2026-10-24 · 结果：`offline_c_player_compatibility_passed`

[打开离线审计工作台](../artifacts/expression-e1-17-player-20261024/index.html) · [机器可读报告](../artifacts/expression-e1-17-player-20261024/report.json) · [E1.16 实际包审计](expression-e1-16-repack-20261024.md)

## 结论

E1.16 候选资源包中的 `Happy.eaf` 和 `shocked.eaf` 已交给与官方路径锚定的 C 解码器、动画计时器和 RGB565 渲染器进行主机端审计。两套资源均覆盖所有图像帧，并通过：

- EAF 图像帧/尾部元数据帧识别；
- 官方 `eye_anim` 自动镜像布局（360×360 圆屏场景）；
- 每一帧 C 渲染像素与离线参考一致；
- 元数据帧拒绝不越界、不泄漏分配；
- 非循环结束事件与循环回到第 0 帧的计时器轨迹；
- 停止/重新开始、重复加载和释放后的保护区与分配计数检查。

E1.16 的语义改动仍只有 `surprised: Happy.eaf → shocked.eaf`，20 FPS、循环、`look_around` 动作和 `Happy.eaf` 其他语义保持不变。E1.17 不创建新动画，也不改变生产播放器源码。

## 安全边界

本轮只读离线：未访问 COM4/COM8，未构建生产固件，未写入 C61、NVS、App 或 assets 分区。详细 C 运行日志、编译 DLL 和原始 EAF 留在项目私有 `tmp`，公开报告只保留摘要和哈希。

播放器闸门通过不等于以下事项通过：

1. 你对 `surprised → shocked` 的视觉语义采用；
2. 仅本体 assets 分区的明确刷写授权、完整回读和回退准备；
3. 刷后圆屏、菜单/对话、摸头、睡眠/唤醒和底座维修后的实机回归。

## 复现

```powershell
python scripts/audit-expression-e1-17-player.py
```

脚本默认读取本项目 `tmp` 下的 E1.16 私有候选包，并从相邻 DeskBot 项目目录加载已锚定的官方 C 播放器审计依赖。缺少私有依赖时脚本会拒绝并保持 `deployment_eligible=false`，不会退化为 Python 静态解析或执行刷写。

## English summary

E1.17 runs the E1.16 `Happy.eaf` and `shocked.eaf` members through the hash-anchored Espressif C decoder, animation timer and RGB565 renderer. Every image frame passed the eye-animation layout, pixel, metadata-boundary, loop/non-loop, stop/restart and reload/cleanup contracts. This is a host-only compatibility gate: no COM port, firmware build, NVS, C61, assets write or deployment authorization was used.
