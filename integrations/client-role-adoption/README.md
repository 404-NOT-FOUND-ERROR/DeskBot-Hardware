# 客户端形态采用

主人确认的外壳配置进入下一次聊天、表达提示和已支持音色。持续个体、生活、记忆和关系沿用客户端原存档。设计与结果见 [client-role-adoption.md](../../docs/client-role-adoption.md)。

## 应用与验证

使用 Node 24 和客户端 `0d8d4c5 + 已复核 4b1a` 的独立目录：

```powershell
node integrations/client-role-adoption/apply.mjs '<客户端目录>'
$env:DESKBOT_HANDOFF_CLIENT = '<客户端目录>'
node --test tests/client-installation.test.mjs tests/client-role-adoption.test.mjs
node --test tests/client-role-adoption-apply.test.mjs
node scripts/verify-client-role-adoption.mjs --client-root '<客户端目录>'
```

脚本接收独立客户端 Git 仓库根目录，先核对源码与 4b1a 恢复依赖摘要，重复执行返回 `already_applied`。Windows 长短路径统一后比较，历史归档作为只读对照。`manifest.json` 保存前后摘要，补丁修改聊天、提示词、语音入口、可选服务注入与配套测试，新增安装解析器。当前开发仓库中的其他生活模块独立维护。

启动组合可参考 `integrations/client-installation/server.mjs`：用原接收器的 `getInstallation` 创建 `createInstalledPresentationResolver`，以 `resolveInstalledPresentation` 注入 `createDeskBotServer`。映射目录以 `provider:voice_id` 为键，填写实际服务和音色。Windows 适配使用 `windows-system-speech` 与本机已安装音色名。

聊天省略 `presentation_installation_id` 时读取当前档案；语音在 `listen.start` 保存安装编号，显式 `null` 表示开始时没有安装。原回合保存快照并用于重试、重开和输出恢复。`presentation_adoption.voice` 中映射支持与 `synthesis` 的实际回报分别记录。

## 本轮范围

离线联测采用独立 SQLite、临时本机 HTTP 和本机音色合成。运行中的 `4314 / 8782 / 8780` 沿用当前版本。下一轮把复核后的模块接入原启动器，显示采用结果，再安排主人体验。
