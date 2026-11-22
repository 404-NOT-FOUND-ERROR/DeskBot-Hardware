# DeskBot Hardware

[简体中文](README.md) · [English](README.en.md)

**换一个外壳，让桌宠成为新的角色。**

基于 ESP-VoCat 的桌宠固件与表达工作台。桌宠识别外壳、提供身体状态并执行表达；电脑客户端管理角色、声音和记忆；工作台用于制作、预览、绑定和发布表情与动作。

## 当前进度 · 2026-11-10

当前实现状态、验证范围和距目标的剩余工作见[项目进度](docs/project-status-20261110.md)。

| 部分 | 已有成果 | 接下来 |
| --- | --- | --- |
| 桌宠本体 | 小智对话、触摸、睡眠和官方动作保留；E3f-4c / E1.18 布局切换与菜单验收 | 验收已安装的 E3f-4e 与 E1.45 资源组合 |
| 表情工作台 | 节点预览、素材替换、打包、容量检查、真实目录、进食/平静请求 | 角色节点接入播放，支持逐素材布局 |
| 设备接口 | USB 身份、会话、显示状态、目录和播放回执 | 开放害羞与常用表达，补当前 37 项目录整链回归 |
| 外壳与角色 | 外壳识别器、置信度和首轮维修后磁场样本；角色绑定协议 | 重复拆装评估、登记保存、客户端确认与角色切换 |
| 对话服务 | 本体继续运行小智；DeepSeek 网关文本骨架 | 接入客户端人格、语音识别/合成和角色声音 |

设备记录为 **E3f-4e App + E1.45 资源**；App 回读、保护区、启动、37 项目录与三次播放下发通过，圆屏及日常交互待验收。首帧修复已有 80 项播放器测试、34 项部署与事务测试、双配置构建和独立审核。后续先完成表达播放，再接外壳角色与对话服务。

## 工作台与可视化

![行为、表情语义、EAF 资源、圆屏布局与动作分支](docs/images/expression-pipeline.svg)

| 入口 | 用途 |
| --- | --- |
| [DeskBot Studio](artifacts/deskbot-studio-e3d2-20261105/index.html) | 节点、素材、角色、发布、设备状态、快照与实时目录 |
| [节点预览](artifacts/expression-node-preview-e1-19-20261024/index.html) | 查看动画、替换前后效果及旧连接与新连接 |
| [角色表达编辑器](artifacts/role-expression-workbench-e1-31-20261029/index.html) | 为不同角色编辑语义与动画映射 |
| [资源目录与草稿对照](docs/deskbot-studio-e3d1-20261105.md) | 28 个表情语义、18 个 EAF、动作关联和逐项预览 |
| [设备实时目录](docs/deskbot-studio-e3d2-20261105.md) | 同一 USB 会话分页读取、完整摘要与连接状态 |
| [USB 使用说明](docs/deskbot-studio-e3c6-20261105.md) | 本机启动、状态查询与表情回执 |
| [完整可视化索引](docs/visualizations.md) | 各阶段的预览、审阅和机器报告 |

下载仓库后可直接打开公开 HTML，或启动本机工作台：

```powershell
python -m pip install -r tests/requirements-body-facts-validation.txt
& .\scripts\start-deskbot-studio-e3d2.ps1
```

启动脚本分别核对离线环境与串口环境，页面地址为 `http://127.0.0.1:8772/`。新电脑可使用 `serve-deskbot-studio-e3d2.py --public --serial-python <含 pyserial 的 Python 路径>` 启动公开版。页面包含官方资源预览、E1.18 已验证快照与独立的实时目录区域；设备 E3d-2 部署验收后可读取实时清单。打包与 USB 环境见使用说明。新害羞候选的图像授权正在核对，公开页面保留来源、映射和容量数据。

## 完整产品链路

```mermaid
flowchart LR
    A[安装固定磁铁布局的外壳] --> B[桌宠识别外壳与身体状态]
    B --> C[客户端匹配角色\n新外壳由主人确认]
    C --> D[角色档案\n人格、音色、记忆和表达映射]
    D --> E[DeepSeek 与语音服务]
    E --> F[桌宠播放声音、表情、灯光与动作]
    F --> G[客户端记录反馈与角色演化]
    W[表达工作台\n制作、预览、绑定和发布] --> D
    W --> F
```

图中是整体目标。当前收尾表情播放和节点控制；底座已修好，外壳登记独立排期；客户端角色与语音随后接入。

## 后续开发顺序

1. **完成表达执行**：验收 E3f-4e，开放害羞与常用表达，连接角色节点、逐素材布局和真实目录。
2. **丰富资源与动作**：把选定新素材装入资源包，增加已有动作的独立入口和组合。
3. **完成换壳角色闭环**：固定布局重复拆装、档案保存、主人确认及客户端激活角色。
4. **接入人格与声音**：连接客户端 Agent、语音识别和合成，再完善记忆与演化。

每轮完成：明确范围 → 离线测试 → 独立构建与差异核对 → 备份与部署 → 实机验收。见[完整开发计划](docs/development-plan-20261104.md)、[架构](docs/architecture.md)与[行动指南](docs/development-pipeline-20261102.md)。

本机工作台当前使用 E3f-4b，沿用唯一串口所有者。版本、资源和恢复入口见[当前基线](docs/baseline-status.md)。GitHub 在较大版本稳定后同步。

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

O2f2 与 E1.13 保留为历史验收锚点。现场备份、NVS、原始串口日志、凭据与构建缓存保存在本机。见[本次发布说明](docs/publication-20261105.md)。

## 许可与来源

本项目新增文档、协议与工具使用 [MIT](LICENSE)。ESP-VoCat、小智、ESP-IDF 组件、字体和第三方动画沿用各自许可证，见 [THIRD_PARTY.md](THIRD_PARTY.md)。
