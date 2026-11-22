# DeskBot Hardware

[简体中文](README.md) · [English](README.en.md)

**同一个体持续成长，让共同经历成为可触摸的形态。**

基于 ESP-VoCat 的桌宠固件与制作工作台。客户端负责生活、记忆与成长，桌宠负责感知和表达；工作台制作、预览并安装角色资源。换壳保留身份、记忆和关系，磁场提供候选，主人确认实际安装。

## 当前进度 · 2026-11-22

**当前重点是高清分层表情验收。** 小智语音、联网摸头、表情下发和主人确认换壳已接通；客户端独立开发自主生活与成长。见[当前主线](docs/project-mainline.md)、[架构](docs/architecture.md)与[本轮发布](docs/publication-20261122.md)。

| 部分 | 已有成果 | 接下来 |
| --- | --- | --- |
| 桌宠本体 | E3f-9d.7 合并联网基线已验收；分层 r7 已安装并核对 | 分层画面与原交互验收，电池和持续运行 |
| 高清表情 | 29 种表情，480→360，完整首尾、途中换情绪与取消，左右眼独立 | 圆屏清晰度、节奏和流畅度观察 |
| 制作工作台 | 统一视觉、节点、逐素材镜像、同包预览、安装与恢复 | 真实角色制作、参数 CAD 与打印装配 |
| 身体与客户端 | USB／Wi-Fi 表情、联网摸头入档、安装同步已接通 | 设备凭证与长期运行 |
| 语音与日常 | 沿用小智；近况查询、语音分享入档已验收，统一后台已部署 | 共同记忆更正、实际登录自启及自然交流观察 |
| 外壳与成长 | A/B 主人确认已验收，模拟磁场基线已保存；自主生活由客户端开发 | 生活愿望联合体验、真实外壳制作与拆装校准 |

当前设备为 **E3f-9d.7 基线 + 高清分层 r7**，32 MiB Flash / 16 MiB PSRAM。完整资源包 4,145,773 B，8 MiB 分区余 4,242,835 B。待机实测约 18 fps；17 项专项测试、浏览器检查和 USB／Wi-Fi 播放回执通过，实屏验收接续进行。此前全脸 EAF 版已验收并保留回退材料。[接入与验收步骤](docs/layered-face-integration.md)

## 工作台与可视化

![行为、表情语义、EAF 资源、圆屏布局与动作分支](docs/images/expression-pipeline.svg)

| 入口 | 用途 |
| --- | --- |
| [高清分层表情预览](artifacts/layered-face-20261122/index.html) | 当前 29 种表情、左右眼节奏、途中切换与收尾 |
| [当前工作台使用](docs/layered-face-integration.md) | 本机连接、资源预览、圆屏播放与验收 |
| [DeskBot Studio 历史预览](artifacts/deskbot-studio-e3d2-20261105/index.html) | 节点、素材、角色、发布、设备状态、快照与实时目录 |
| [节点预览](artifacts/expression-node-preview-e1-19-20261024/index.html) | 查看动画、替换前后效果及旧连接与新连接 |
| [角色表达编辑器](artifacts/role-expression-workbench-e1-31-20261029/index.html) | 为不同角色编辑语义与动画映射 |
| [资源目录与草稿对照](docs/deskbot-studio-e3d1-20261105.md) | 28 个表情语义、18 个 EAF、动作关联和逐项预览 |
| [设备实时目录](docs/deskbot-studio-e3d2-20261105.md) | 同一 USB 会话分页读取、完整摘要与连接状态 |
| [USB 使用说明](docs/deskbot-studio-e3c6-20261105.md) | 本机启动、状态查询与表情回执 |
| [完整可视化索引](docs/visualizations.md) | 各阶段的预览、审阅和机器报告 |

本机当前入口：[8782 工作台](http://127.0.0.1:8782/#connection) · [高清表情](http://127.0.0.1:8782/layered-face/) · [4311 桌宠与共同记忆](http://127.0.0.1:4311/companion)。工作台使用唯一 USB 所有者；日常后台统一小智工具、正式存档、摸头和联网表情。客户端位于[独立仓库](https://github.com/404-NOT-FOUND-ERROR/DeskBot)。

仓库内公开 HTML 可直接查看。以下脚本启动早期 E3d2 公开预览，地址为 8772；当前接入流程见上方说明：

```powershell
python -m pip install -r tests/requirements-body-facts-validation.txt
& .\scripts\start-deskbot-studio-e3d2.ps1
```

启动脚本分别核对离线环境与串口环境。新电脑可使用 `serve-deskbot-studio-e3d2.py --public --serial-python <含 pyserial 的 Python 路径>` 启动公开版。页面保留官方资源预览、E1.18 历史快照与实时目录区域；素材来源与许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 完整产品链路

```mermaid
flowchart LR
    A[现实输入与主人交流] --> B[客户端：同一个体持续生活]
    B --> C[声音 / 字幕 / 表情 / 动作]
    C -->|实际互动与结果| B
    B --> D[经历 / 偏好 / 愿望]
    D --> E[讨论形象 / 参数化 / 打印]
    E --> F[主人确认安装 / 采用新形态]
    F --> B
    W[制作工作台] --> E
    M[磁场候选 / 手动入口] --> F
```

图中是完整产品目标。身体联网与小智生活接入已有验收，持续生活和真实制作按下方顺序联合交付。

## 后续开发顺序

1. **验收高清分层表情**：画面、完整首尾、中途切换、取消，以及菜单、对话、摸头与睡眠回归。
2. **完善日常运行**：统一后台、实际登录自启、设备凭证、网络变化、电源与持续运行。
3. **完善自然交流**：小智近况、语音分享与共同记忆使用同一个体存档，补齐更正和日常体验。
4. **联合生活与愿望体验**：客户端独立完成生活成长验证，双方验收可感知的实际经历与愿望。
5. **完成真实制作与采用**：形象方案、参数 CAD、打印试装、配套角色包和安装接续。

每轮完成：明确范围 → 离线测试 → 独立构建与差异核对 → 备份与部署 → 实机验收。见[完整开发计划](docs/development-plan-20261104.md)、[架构](docs/architecture.md)与[行动指南](docs/development-pipeline-20261102.md)。

开发沿用唯一串口所有者。版本、资源和恢复入口见[当前基线](docs/baseline-status.md)。GitHub 在较大版本稳定后同步。

## 开发与验证

```powershell
python scripts/check-publication.py
python scripts/run-offline-checks.py
```

离线检查使用 Python 3.11+；原生 C/C++ 测试需要编译器。固件使用 ESP-IDF 5.5.5，见 [firmware/body](firmware/body/README.md)。Windows 本机先运行 `scripts/preflight-deskbot-toolchain.ps1` 核对环境。测试、构建、安装记录和人工验收分别保存。

| 目录 | 内容 |
| --- | --- |
| `firmware/body` / `firmware/releases` | S3 固件、锁定依赖与 N32R16 目标配置 |
| `firmware/base` | C61 历史增量与上游说明 |
| `protocol` | 身体事实、外壳、角色、表达和 USB 契约 |
| `scripts` / `tools` / `tests` | 构建、工作台、诊断与回归测试 |
| `artifacts` / `docs` | 公开预览、结果、架构与路线 |
| `gateway` | 早期客户端连接原型 |

O2f2 与 E1.13 保留为历史验收锚点。现场备份、NVS、原始串口日志、凭据与构建缓存保存在本机。见[本次发布说明](docs/publication-20261122.md)。

## 许可与来源

本项目新增文档、协议与工具使用 [MIT](LICENSE)。ESP-VoCat、小智、ESP-IDF 组件、字体和第三方动画沿用各自许可证，见 [THIRD_PARTY.md](THIRD_PARTY.md)。
