# E3f-4e：表情切换的首帧重排

更新：2026-11-10。COM4 的 E3f-4e App + E1.45 已通过主人圆屏与日常交互验收：表情切换、菜单、对话、摸头与睡眠均正常，见[验收记录](../artifacts/studio-reflow-e3f4e/manual-acceptance.json)。App 回读、保护区、启动身份、37 项目录和进食→平静→进食下发核对通过。前序资源安装见 [E3f-4d](resource-installation-e3f4d.md)。

## 目标与设计

平静为 160 像素高，害羞为 200；两者都使用单眼布局。GFX 先更新尺寸、回调，再按旧坐标标记刷新区域，绘制时才计算新位置。首帧重排必须同时覆盖布局和尺寸变化。

沿用 `Configure / Play / OnFrame / Reset` 接口。新增默认关闭的 `DESKBOT_ASSET_REFLOW_ENABLE`，依赖逐素材布局开关。关闭版复现 E3f-4c；开启版每次选择动画时读取有边界的 EAF 首图像信息。布局或尺寸变化、首次播放、前一切换仍待处理时，安排首图像就绪后的重排与完整刷新；相同尺寸普通播放沿用原绘制路径。该次刷新沿用现有 512 回调预算，后续帧继续原绘制。

数据流：资源语义 → 原 `Play` 图形锁 → 首图像尺寸与回调计数 → 原播放器 → `OnFrame` 核对尺寸 → 重排与刷新。原调用失败时恢复前一布局和待处理状态；新播放替换旧计数；元数据与其他对象回调各自处理；卸载清空登记。

依赖为冻结的 E3f-4c 源码、当前 E1.45 资源、原 Emote/GFX、已有 IDF 锁文件与工具链。候选仅增加重排条件与配置开关。菜单 DMA 修复、USB 接收任务、请求入口、目录与资源格式沿用当前实现。

## 验证与交付

| 层次 | 检查 |
| --- | --- |
| 原生测试 | E1.18 与 E1.45；160↔200、完整画面↔单眼、首次播放、连续和首帧前快速切换；首图像前元数据；失败重试与卸载 |
| 构建 | 关闭版执行和数据与 E3f-4c 一致；开启版核对源码范围、保护产物、内部静态数据和函数栈 |
| 目录与调度 | E1.45 的 37 项目录一致；原单条请求、图形锁与回执语义一致 |
| 实机接续 | 下一轮 App 安装后观察画面、菜单、对话中及结束后切菜单、摸头和睡眠唤醒 |

测试执行生产 C++ 布局与目录代码，GFX/Emote 使用替身；圆屏效果在实机阶段确认。源码、测试、构建和记录由另一位 AI 复核后归档。下一增量再开放害羞电脑入口，然后接角色节点播放。

## 当前结果

实现与双配置构建已完成，候选仅变更配置开关与布局模块。80 项候选测试通过，37 份相关源摘要前后一致；E1.45 的 37 项目录保持一致。另一位 AI 独立复测四项关键测试通过，并核对了官方回调顺序与 19 份 EAF 的首图像格式。

关闭版的程序与数据复现 E3f-4c；开启版 App 为 4,521,760 B，仅增加 16 B，分区余量 1,622,240 B。内部静态数据不增加，最大核对函数栈为 1,392 B；bootloader、分区表、构建参考资源和模型产物一致。见[构建报告](../artifacts/studio-reflow-e3f4e/firmware-build-report.json)。

冻结 E3f-4c 的新矩阵对照保留在 `control-1.json`；首轮候选的两项故障恢复统计失败保留在 `validation-1.json`。现在分别检查首帧、恢复帧和整段覆盖，正常路径要求全帧完整，故障路径要求后续帧恢复且播放器只调用一次。最终候选结果见[80 项测试](../artifacts/studio-reflow-e3f4e/offline-validation.json)与[源码差异](../artifacts/studio-reflow-e3f4e/source-delta.json)。

App 安装、自动核验和主人实测已完成，下一步接入[害羞电脑入口](studio-shy-installation-e3f5.md)。

## 安装接续

本轮新增 `studio_reflow_deployment` 版本适配器和 `deploy-studio-reflow-e3f4e.py` 入口，复用已有 App 事务的准备、安装、校验、启动和恢复接口。

数据流：E3f-4d 已安装记录与完整资源覆盖 → E3f-4c / E1.45 参考快照 → 现场身份、实时 NVS 和全片摘要 → 本轮计划 → App 写入与回读 → 启动身份与目录核对。写入范围为 `0x10000..0x460000`；恢复使用本轮保存的 E3f-4c 完整 App 擦除范围，E1.45 继续保留。

依赖为已审阅的 E3f-4e 镜像、双配置构建报告、80 项离线结果和原事务工具。安装前核对这些文件及冻结源码摘要；恢复独立使用持久备份。测试覆盖安装/恢复、写入及启动失败、NVS 运行更新、资源与尾扇区变化、目标身份、旧计划和候选缺失。独立 AI 审核通过后执行安装，再由主人观察圆屏与日常交互。

34 项部署与事务测试通过，另一位 AI 独立复测 5 项通过。现场计划 `01a3670df3ebd1c04aade209ea59cd81ac015827716eb8e5bc27625aa2071be6` 已完成安装，App 完整擦除范围逐字节回读一致；NVS、资源和其余静态区域一致，启动身份匹配候选。工作台重连后 37 项目录与预期一致，三次播放各有同会话关联下发回执。

恢复入口：`scripts/deploy-studio-reflow-e3f4e.py recover --run e3c-live-20261110-e3f4e-live1`，使用 IDF Python 3.11。本轮备份在同名 `tmp/` 目录；先恢复 E3f-4c App，E1.45 继续保留。之后如需恢复 E1.18，使用 E3f-4d 的资源恢复入口。

记录：[部署测试](../artifacts/studio-reflow-e3f4e/deployment-validation.json)、[部署审核](../artifacts/studio-reflow-e3f4e/deployment-peer-review.json)、[现场结果](../artifacts/studio-reflow-e3f4e/live-result.json)、[真实目录](../artifacts/studio-reflow-e3f4e/live-directory.json)、[下发回执](../artifacts/studio-reflow-e3f4e/live-dispatches.json)。
