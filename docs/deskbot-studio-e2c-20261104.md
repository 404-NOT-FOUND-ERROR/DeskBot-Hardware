# DeskBot Studio E2c · 表达请求模拟

2026-11-04。阶段二完成：在同一工作台里编辑表情、生成角色资源包，并在电脑上测试“发出请求 → 看见回执”。

## 怎么使用

打开[本机工作台](http://127.0.0.1:8766/#connection)，选择“连接测试”。

1. 点击“连接模拟器”，读取已保存的 E1.18 表达目录。
2. 选择表情，查看圆屏预览，点击“发送模拟请求”。
3. 在右侧查看“已受理”和“已提交执行”；重发同一请求，可看到去重结果。
4. 切换“对话占用”“调度延迟”或“底座离线”，查看对应结果。
5. 断开再连接，核对最近结果；也可导出目录和本轮记录。

节点编排、素材库、角色和发布继续使用原有操作。320px 与 390px 窄屏完整显示六个导航入口，预览保持正圆；支持深色主题与系统减少动态效果设置。

```mermaid
flowchart LR
    A[读取表达目录] --> B[选择表情或现有动作]
    B --> C[生成请求编号]
    C --> D[模拟调度]
    D --> E[查看执行回执]
    E --> F[重连后同步结果]
```

## 目录和回执

目录包含 **29 个表情语义、18 个当前动画资源和 5 个现有动作**。其中 28 个语义可请求；聆听动画继续由语音状态管理。新害羞候选保留在创作草稿与资源发布流程里，模拟目录展示 E1.18 已保存映射。

| 页面结果 | 含义 |
| --- | --- |
| 已受理 | 请求正在等待表现模块处理 |
| 已提交执行 | 请求已交给显示或底座模块 |
| 忙碌、过期或中断 | 本次请求未提交执行，页面显示具体原因 |
| 返回原结果 | 同一请求编号重发，复用原决定 |
| 客户端超时 | 等待时间已到；重新同步后查询结果 |
| 收到动作完成事实 | 底座提供的单独事实；原协议没有关联请求编号 |

表情和动作分别请求。页面展示现有事件路由关联的动作，供后续绑定设计使用。当前连接目标是电脑模拟器，设备接口进入下一阶段实现。

协议分别为 [`deskbot.expression_catalog/v1`](../protocol/deskbot-expression-catalog-v1.schema.json) 和 [`deskbot.expression_control/v1`](../protocol/deskbot-expression-control-v1.schema.json)。目录按内容摘要标识；请求记录目标、启动周期、会话、递增序号、目录版本、角色、编号和有效期。队列上限 1 条，最近事件 24 条，每轮去重决定 64 条。

## 复现

从实际 Git 根目录运行。离线环境通过预检返回 Python；Node 与浏览器路径见[行动指南](development-pipeline-20261102.md)。

```powershell
$DeskBotEnv = & .\scripts\preflight-deskbot-toolchain.ps1 -Mode Offline | ConvertFrom-Json
$DeskBotPython = $DeskBotEnv.offline_python.path
& $DeskBotPython scripts/build-deskbot-studio-e2c.py --check
& $DeskBotPython scripts/build-deskbot-studio-e2c.py --local --check
& $DeskBotPython -m unittest discover -s tests -p test_expression_control.py
& $DeskBotPython -m unittest discover -s tests -p test_deskbot_studio_e2c.py
node --test tests/deskbot-studio-e2c-core.cjs
```

首次生成页面时去掉 `--check`。本机版读取本地候选图集；公开版可以独立生成。

```powershell
& $DeskBotPython scripts/serve-deskbot-studio-e2c.py
```

服务启动后，在另一个终端运行浏览器检查：

```powershell
node tests/deskbot-studio-e2c-browser.cjs
```

本轮 **22 项 Python、18 项 Node、25 项浏览器检查全部通过**；编辑与发布核心的另 25 项回归通过。浏览器覆盖公开页与本机页、请求结果、重复编号、忙碌、过期、断线重连、模拟重启、动作事实、节点编辑和资源打包，以及桌面/窄屏和动态效果。29 份工作台页面的脚本、JSON、链接及回环 HTTP 检查通过。

完整离线套件 816 项：809 通过、2 跳过、1 失败、4 错误。5 项未通过检查对应 E1.26/E1.43 的历史源码锁与当前源码差异，具体项目和原始日志摘要见[验证记录](../artifacts/deskbot-studio-e2c-20261104/verification.json)。本轮硬件访问和设备写入均为 0。

## 接下来

阶段三第一步核对本体现有通信入口、任务和状态来源，选取一个只读状态接到工作台。随后接入表达请求、回执和既有表现调度。外壳登记在底座修复后继续；角色绑定完成后再接 DeepSeek 与语音服务。完整步骤见[开发计划](development-plan-20261104.md)。
