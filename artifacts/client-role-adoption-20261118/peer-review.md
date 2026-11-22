# 形态采用 · 独立复核

日期：2026-11-18。结论：本轮离线交付通过，无待处理的 P0／P1／P2 问题。

核对范围：`integrations/client-role-adoption/`、原安装接收器、语音会话与 Windows 合成适配、客户端本轮接入点、自动验证脚本，以及设计、架构和开发主线。工作客户端中的其他生活模块保持原状。

- 回合在异步工作前固定安装配置；语音区分省略编号、显式无安装和缺失历史档案。
- prepared 与 output_enqueued 恢复复用原回答、原配置和输出编号；持续身份与生活记录延续。
- 人格和说话风格进入原提示词；音色映射、请求与实际合成回报分别记录。
- 补丁核对目标源码、恢复依赖、补丁及模块摘要；重复执行幂等，本地改动与缺失依赖停止应用，历史基线保持保留用途。Windows 长路径与 8.3 别名经真实路径统一，仓库子目录误用有测试。
- 多设备未指定目标时返回明确的设备选择要求；旧个体编号兼容。

首轮发现的历史基线漂移、恢复依赖核对遗漏与多设备歧义均已修正，并加入验证。

最终核对 `report.json` 的全部交付源码摘要与当前文件一致；原 4b1a 基线全部历史摘要一致，恢复依赖与多设备 409 检查已纳入当前验证。

独立执行：`node scripts/verify-client-role-adoption.mjs --client-root tmp/client-role-adoption-20261118/reproduced-v6 --output artifacts/client-role-adoption-20261118/peer-recheck-final`。

结果：110／110 通过，0 失败、0 跳过；客户端与恢复 38 项、安装与采用 34 项、语音会话与原客户端 38 项，退出码 0。包含 Windows System.Speech 指定 Microsoft Huihui Desktop 的真实本机合成。详细证据见 [独立复跑结果](peer-recheck-final/report.public.json)。

本轮完成离线模块与复现包。运行服务、设备连接和固件保持当前状态；下一步组合原启动器并展示采用结果，再安排主人体验。
