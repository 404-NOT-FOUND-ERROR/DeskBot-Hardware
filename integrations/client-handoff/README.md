# 客户端接入交接包

目标：客户端按桌宠已验收的能力开发，两边用同一组检查核对接入。自主生活、记忆与关系由客户端保存；本仓负责身体感知、表达执行、资源和回执。

## 接入顺序

```mermaid
flowchart LR
    C[客户端：同一个体与生活] -->|表达意图| Q[原 HTTP 输出队列]
    Q --> A[共用表现协调入口]
    A --> D[唯一 USB 所有者 / 桌宠]
    D -->|实际执行阶段| A
    A -->|原命令回执| Q
```

当前可复用的是 `neutral / happy` 表情链路，设备基线为 E3f-9d.3 双角色版。其他能力与接续范围见[接入基线](../../docs/client-hardware-integration-20261116.md#给客户端的接入基线)；机器可读摘要见 [contract.json](contract.json)。

## 接口与归属

| 接口 | 请求与结果 |
| --- | --- |
| 工作台 `POST /api/client-presentation/chat` | `event_id / correlation_id / message`；适配层冻结设备与当前表现版本后，调用原客户端 `/api/chat`。请求头为 `X-DeskBot-Studio: client-presentation-v1` |
| 客户端 `POST /api/chat` | 上述字段加 `character_id / device_id / role_revision`。同次输入重试沿用原编号、正文和版本 |
| 客户端 `POST /api/devices/hello` | 登记设备及 `capabilities: {"display.expression": true}` |
| 客户端 `GET /api/outbox` | 表情消费者按 `target=vocat`、设备、`type=render.expression`、`source_event_prefix=reply-` 取队列；声音由对应消费者处理 |
| 客户端 `POST /api/outbox/:command_id/ack` | `command_id / status / device_id / target / source / occurred_at / correlation_id / result`；失败附 `error.code` |

`character_id` 是持续个体；设备的 `role_id` 是表现资源。`role_revision` 由适配层对冻结的设备、角色、图摘要与代次等绑定取 SHA256，客户端原样带回命令。ACK 的该值放在 `result.role_revision`；原队列将 `result` 保存为 `acknowledgment.payload`。

USB `accepted` 表示受理，`dispatched` 表示已提交播放器。后者结束本次提交命令，结果为 `completion_scope: player_dispatched`；循环动画的画面另做实机观察。角色切换的目标、首帧和保存沿用现有独立回执。

适配层复用现有协调入口、提交账本和 USB 协议。菜单／对话占用时等待；待提交命令期限为排队后 30 秒与原命令期限的较早者。表现变化后结束旧代次请求。未映射语义返回 `unsupported_expression`；已提交但结果未知时记录 `delivery_unknown`，重连核对原记录。

## 离线兼容检查

依赖：Python 3.11+ 标准库、Node 24（含 `node:sqlite`）、Git，以及待核对客户端的源码。使用明确的本机运行路径，无需安装 npm 包。

在本仓根目录运行，替换三个路径：

```powershell
& '<Python路径>' scripts/check-client-handoff.py `
  --client '<客户端源码目录>' `
  --node '<Node路径>' `
  --output 'artifacts/client-handoff-本次编号'
```

检查器启动客户端原 HTTP 服务与独立 SQLite，使用固定回答、模拟时钟和生产 ControlLink 内存设备。检查平静／开心、输入去重、回执阶段、其他消费者队列保留、菜单占用、版本替换和回执恢复，并复跑现有适配层单元测试。客户端源码可为开发中的版本，结果记录实际提交与源码摘要。

输出包括 `report.json`、逐项测试记录和真实 HTTP 往返示例 `wire-examples.json`。退出码 `0` 表示本轮检查通过，`1` 表示存在失败；已有输出目录保持原记录。检查完成后清理临时数据库。设备观察、真实语音和长期生活效果分别验收。

## 下一步

“我换好外壳了 → 主人确认 → 资源核对 → 设备表情采用”已通过实机验收，分别显示目标、实际表情与保存结果。接口见[安装确认](../../docs/shell-installation-confirmation.md)。[客户端安装同步](../client-installation/README.md)接收同一安装编号与完整配置，使用客户端原 SQLite 保存最近已同步形态。[运行接入](../../docs/client-installation-runtime.md)已接通自动同步与安装卡的客户端接收结果。人格、实际音色与成长阶段采用继续按原入口联合接入，两边结果齐备后更新联合验收状态。磁场候选可辅助确认，手动入口可直接使用。
