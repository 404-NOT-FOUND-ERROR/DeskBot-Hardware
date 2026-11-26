# 小智语音接入 DeskBot

2026-11-19。日常语音优先沿用已实测的小智云。DeskBot 保存同一个体的生活、记忆与成长。第一步让主人用原来的声音听到一条真实近况。

2026-11-20：日常入口改为客户端的[桌宠与共同记忆](http://127.0.0.1:4311/companion)。旧独立桥接已交接，客户端加密保存绑定，并在正式后台重开后自动连回小智。下文的独立脚本保留为复跑入口；同一个小智接入点使用一套桥接即可。

2026-11-22。主人确认近况、语音记事和更正后回忆正常。后台核对原输入和更正稿对应同一条记录，小智最后一次查询成功。形态将按实际外壳安装另行验收。[本轮实机记录](../artifacts/companion-memory-live-20261122/acceptance.public.json)

| 路线 | 能做到什么 | 决定 |
| --- | --- | --- |
| 小智云整套对话 + MCP | 沿用原听写、回答、音色和设备连接。回答时可按需读取 DeskBot 近况 | 小智语音、暂无近况和正式存档播报已实测 |
| 小智云的听写和发声单独接入 | DeskBot 可以主持每轮对话。已查阅的官方资料提供控制台和设备会话协议，独立听写与合成接口仍待确认 | 取到官方接口后评估 |
| 小智兼容服务 + 独立语音引擎 | DeskBot 可统一管理对话、记忆和打断。当前 Windows 试用低于小智体验 | 需要全量接管时，先用同组语句比较语音引擎 |

小智控制台支持选择模型和音色，自定义角色说明。MCP 在模型选用工具时读取事实，完整对话记录和每轮输入同步是后续独立能力。[官方配置](https://docs.xiaozhi.me/zh/console/) · [官方 MCP 接入](https://docs.xiaozhi.me/zh/mcp/connect-existing-servers.html)

```text
主人问「我刚回来，你最近忙什么？」
  → 小智听写、理解问题
  → MCP 查询 DeskBot 最近完成的一条活动
  → 小智沿用原音色说出近况
```

工具 `deskbot_get_recent_life` 无参数，可绑定同一个体的正式存档。每次调用通过只读 SQLite 连接检查存档标记 `live/world` 和个体编号，再从已保存的生活结果中挑出最新完成的一条。小智收到 `available`、活动标题和北京时间日期。`recency=recent` 表示距结束不超过 24 小时，`recency=previous` 表示上一次经历，播报时带上日期。没有已完成的活动时返回 `empty`，来源不可读或身份不符时返回 `unavailable`。

联测可继续使用 `GET /api/life/development?actor_id=…&limit=64`，沿用 24 小时内最新活动的规则。正式存档模式直接读取客户端已提交的记录，允许正式生活服务停机后讲述过去的经历。当前最新一条是北京时间 2026 年 10 月 26 日 05:37 的休息记录。

7 项电脑端测试通过，覆盖正式存档的个体绑定、北京时间日期、24 小时边界、工具发现和客户端原接口。[桥接器源码](https://github.com/78/mcp-calculator/tree/c537f71d61fd73b47d6c8955b5df6d3721acf4e4)

实机沿用原小智语音。11 月 19 日 17:13，小智查询正式存档并收到 `available`，主人听到了“10 月 26 日”和休息，与同一个体的记录一致。[正式存档实机验收](../artifacts/xiaozhi-cloud-life-bridge-20261119/life-archive-acceptance.json) · [原语音恢复记录](../artifacts/xiaozhi-cloud-life-bridge-20261119/hardware-acceptance.json)

1. 在 Git 根目录用 PowerShell 7.2+ 检查依赖。首次运行 `-Prepare`。本机已核对 Node 24、Python 3.13 和桥接器。
2. 打开桌宠所属智能体的 MCP 接入点，将地址粘贴到启动脚本的隐藏输入，保持终端运行。出现“小智已查询近况工具”后开始听测。换源时等待桥接重连，本次约 8 分钟。[小智控制台](https://xiaozhi.me)
3. 问「你在聚形域上一次做了什么？是哪天？」核对终端回执和桌宠说出的活动、日期。[语音地址恢复步骤](xiaozhi-device-voice-trial.md)

```powershell
./scripts/start-xiaozhi-life-bridge.ps1 -Prepare
./scripts/start-xiaozhi-life-bridge.ps1 -Check
./scripts/start-xiaozhi-life-bridge.ps1 -ArchiveDatabase '<客户端正式 SQLite 的绝对路径>'
```

联测复跑时，将 `DESKBOT_XIAOZHI_CLIENT` 设为客户端仓库目录，`DESKBOT_XIAOZHI_BRIDGE_TEST=1`，再运行 `node --test tests/xiaozhi-life-tool.test.mjs`。

### 主人语音分享

主人说“帮我记一下”并分享一段见闻时，小智调用 `deskbot_share_owner_story({text})`。工具返回 `recorded` 后，小智回应“记下了”。客户端按“小智语音转述”保存主人见闻和接收日期。兴趣和愿望继续按生活规则形成。

```text
主人说话 → 小智识别并提取分享 → 绑定 shaping-001
  → 客户端原输入事件与世界入口 → 同一个体的生活记忆 → 保存回执
```

客户端接收提取后的正文 `text`，长度 1–300 字符。同一天的相同内容复用原记录，日期按北京时间计算。近况查询和分享使用同一个个体的正式存档。

依赖 Node 24 及客户端现有 `persistence`、`input-store`、`persistent-world` 模块。分享入口按次接收，使用客户端原事务和记忆规则；正式生活服务恢复后，写入统一交给它的入口。本轮保持客户端当前的生活开发进度。

电脑端 39 项检查通过，覆盖工具发现、事务回滚、重复提交、重开和服务交接。11 月 19 日 19:30，小智返回 `recorded`，主人确认听到“记下了”。正式存档新增一条语音分享，输入正文与同一个体的生活记忆一致，来源为“小智语音转述”。本步实机验收通过。[验收记录](../artifacts/xiaozhi-cloud-life-bridge-20261119/owner-story-acceptance.json)

启动时先在客户端仓库根目录运行下方命令。复用本机凭证文件，内容为 64 个十六进制字符。正式存档打开后，用 `http://127.0.0.1:4311/health` 检查就绪。首次读取这份历史存档约需两分钟。

```powershell
$env:DESKBOT_HOST = '127.0.0.1'
$env:DESKBOT_PORT = '4311'
$env:DESKBOT_RUN_SCOPE = 'live'
$env:DESKBOT_DB_PATH = '<客户端正式 SQLite 路径>'
$env:DESKBOT_OWNER_STORY_ONLY = '1'
$env:DESKBOT_OWNER_STORY_TOKEN_FILE = '<本机凭证文件路径>'
node apps/deskbot-service/src/index.mjs
```

在固件仓库运行桥接脚本，两个路径与客户端一致。小智工具列表出现 `deskbot_share_owner_story` 后，可以说“帮我记一下”并接上一句话。

```powershell
./scripts/start-xiaozhi-life-bridge.ps1 -ArchiveDatabase '<客户端正式 SQLite 路径>' -ShareTokenFile '<本机凭证文件路径>'
```

恢复完整生活服务时，先停止专用接收服务，再用同一存档和凭证启动完整模式。端口保持 4311 时，桥接继续使用原入口。
