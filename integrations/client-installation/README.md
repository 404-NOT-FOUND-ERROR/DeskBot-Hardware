# 客户端安装同步

让客户端接收主人已确认、桌宠已显示并保存的安装结果，延续同一个体。模块使用客户端原 SQLite、设备登记与 HTTP 服务；原生活、记忆、关系和成长记录继续保留。

## 接入

将 `receiver.mjs` 放入客户端，启动时调用：

```javascript
import {createInstallationReceiver, attachInstallationRoutes} from './receiver.mjs';

const receiver = createInstallationReceiver({
  persistence, sourceId: '这份安装记录库的固定编号', characterId,
  getDevice: id => deviceRegistry.get(id),
  currentCharacterId: () => server.persistentWorld.get().protagonist.character_id,
});
attachInstallationRoutes(server, receiver); // 在 server.listen() 之前
```

`persistence` 和 `deviceRegistry` 使用创建原服务的同一实例。新增路由处理安装同步，其他请求继续原处理器。`server.mjs` 是隔离兼容检查启动器，使用模拟时间与固定回答。

| 接口 | 用法 |
| --- | --- |
| `POST /api/hardware/installations` | 提交 `deskbot.owner-installation-handoff.v1`；返回持久接收凭证和当前安装编号 |
| `GET /api/hardware/installation?character_id=…&device_id=…` | 查询最近已同步安装、配置、接收时间与同步时的设备快照 |

POST 请求为 JSON，带 `X-DeskBot-Integration: shell-installation-v1`，最大 128 KiB。输入字段和排序规则见[设计](../../docs/client-installation-sync.md)。

## 同步与复跑

工作台继续管理安装进度，客户端先登记同一设备与持续个体。然后在本仓运行：

```powershell
& '<Python路径>' scripts/sync-client-installation.py `
  --workbench http://127.0.0.1:8782 --client '<客户端本机地址>' `
  --source-id '<安装记录库固定编号>' --character-id shaping-001 --device-id '<设备编号>'
```

退出码：`0` 已同步，`2` 等待当前安装或已有新目标，`1` 请求失败。回应丢失后重跑同一命令，客户端返回原凭证。重开接收端沿用原 SQLite 和来源编号。

兼容检查使用指定的开发版客户端、Node 24+ 以及独立临时 SQLite：

```powershell
& '<Python路径>' scripts/check-client-installation.py `
  --client '<客户端源码目录>' --node '<Node路径>' --output 'artifacts/client-installation-本轮编号'
```

## 运行服务

当前客户端 `4314` 与工作台 [8782/#shells](http://127.0.0.1:8782/#shells)已自动同步。原客户端启动器 `scripts/serve-client-expression-4b1b.mjs` 增加 `--installation-source-id`；共享工作台启动器 `scripts/serve-shared-shell-workbench.py` 同时提供 `--client-url` 和 `--sync-source-id`。沿用原数据库、安装库和固定来源编号。具体结果见[运行接入](../../docs/client-installation-runtime.md)。

后台任务核对当前设备保存结果后提交，失败沿原安装编号重试；服务重开向客户端重新核对凭证。卡片显示当前同步状态和最近接收时间，查询失败时保留历史记录并显示等待。

## 当前采用范围

本轮接收并保存安装事实。查询结果标为 `scope=installation_facts`、`live_device_state=false`；设备信息是同步时的观察。空人格和未配置音色保留现有设置，已配置项等待原聊天与语音入口采用。实物安装与虚拟成长阶段分别记录。
