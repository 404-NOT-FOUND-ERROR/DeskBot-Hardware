# 阶段 1：角色档案与管理

目标：先创建完整角色，再登记外壳。名字、人格、音色配置与表达角色统一保存；预览使用现有素材。阶段 2 接外壳绑定，阶段 4 接真实语音服务。

## 数据与接口

新增 `deskbot.role_profile.v2`：`role_id`、`display_name`、`persona`、`voice`、`expression_role_id`、`expression_graph_sha256`、`preview_catalog_id`、`profile_revision`。音色保存服务类型、音色标识与说话风格。外壳和磁档案保留在独立绑定模块；S2a 的表达角色编号沿用原定义。

电脑以单写者锁、原子替换保存角色列表。创建时修订为 0，保存后递增；编辑携带预期修订。资源版本变化后显示“需重新关联”，由主人选择当前表情版本再保存。旧 v1 导入必须明确选择表达角色；原档案和摘要随迁移记录保留，旧确认状态只作为来源记录。

| 接口 | 内容 |
| --- | --- |
| `GET /api/characters/state` | 角色列表、表达目录、角色图版本、配置进度 |
| `POST /api/characters/save` | `profile`、`expected_revision`；创建或保存角色 |
| `POST /api/characters/migrate` | `legacy_profile`、`expression_role_id`；迁移为独立角色草稿 |

POST 使用同源 JSON 与 `X-DeskBot-Studio: characters-v1`。校验字段、长度、修订和引用；冲突返回 409，保存失败返回 503。表单保留未保存内容，冲突后可载入新版或另存角色。

## 数据流与页面

`我的角色 → 编辑草稿 → 校验引用/修订 → 原子保存 → 重开继续`。

工作台新增“我的角色”。左侧角色列表，右侧名字、人格、音色与表达选择；旁边播放开心、难过、害羞等预览。预览直接读取已有 atlas、镜像规则和三段调度器，切换预览只影响本页。设备实际角色继续由原有设备入口显示。页面标明“电脑已保存”“语音待接入”“外壳待关联”。

依赖：现有表达目录 `role_activation.load_roles()`、留存双角色图、`GrokRoleScheduler`、`DeskBotMirrorRender`、现有 HTTP 服务。现有设备命令与固件沿用。

## 服务接口盘点

| 能力 | 源码现状 | 接续 |
| --- | --- | --- |
| 小智语音连接 | `websocket_protocol.cc` 提供 Opus、hello、会话编号和音频通道 | 阶段 4 复用音频链路 |
| 字幕和表达 | `application.cc` 接收 `stt`、`tts.sentence_start`、`llm.emotion` | 关联同一角色版本 |
| 人格/音色选择及生效回执 | 当前设备协议中未找到对应字段与确认接口；服务端源码未包含在此仓库 | 核对实际服务能力，确定适配接口 |
| 本地网关 | `gateway/deskbot_gateway.py` 提供文本聊天转发 | 按阶段 4 设计接人格与语音 |

本阶段音色为配置；真实试听与会话换角随服务接入交付。

## 验证与记录

单元测试覆盖数据边界、保存重开、修订冲突、保存失败、v1 迁移与版本变化；HTTP 测试核对实际持久化和请求约束；浏览器验证创建、编辑、重开、预览、错误恢复和窄屏。检查角色编辑期间设备命令为零。设计与实现分别由另一 AI 复核，结果记入本文。

设计复核：2026-11-13，`product_pipeline_review` 通过。迁移同一来源和表达版本时返回原导入档案，重复提交可接续；人格等后续编辑保留。

## 交付结果

入口：[我的角色](http://127.0.0.1:8780/#characters)。点击“用圆团创建”或“用原有角色创建”，填写后保存；重新打开可继续编辑。右侧直接预览六种表情及原有镜像规则，圆团沿用进入、持续、退出调度。

- 24 项角色单元/HTTP 测试、21 项外壳单元/HTTP 回归通过；38 项新页面检查、26 项原外壳页面检查通过。原有角色选择、v1 档案与磁档案相关回归通过。
- `product_pipeline_review` 完成设计与实现独立复核，最终通过；修正读取后的表单同步和 schema 引用解析，并补齐回归。HTTP 提前拒绝复用既有 Windows 回复处理方法。
- 本机 8780 已载入新版，真实页面预览通过，测试未产生设备命令。采样按既有记录接续，当前固件沿用 E3f-9d.3。
- 配置保存在 `tmp/deskbot-character-profiles/profiles.json`；页面测试使用独立临时库。本轮未创建主人角色档案，等待按喜好填写。

运行：`scripts/start-deskbot-shell-workbench.ps1 -Preview`；外壳入口使用 `-View shells`。专项测试：配置好的 Python 运行 `-m unittest tests.test_character_profiles tests.test_shell_workbench -q`，Node 运行 `tests/characters-browser.cjs`。schema 测试使用本机已有 `jsonschema` 与 `referencing`。

报告：[交付记录](../artifacts/character-profiles-stage1-20261113/report.json)。下一步：添加外壳、选择完整角色、确认绑定与切换决策，先完成日志和模拟设备联测。
