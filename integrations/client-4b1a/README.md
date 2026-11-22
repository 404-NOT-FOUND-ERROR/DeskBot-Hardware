# 客户端表达接入 · 4b1a

目标客户端：`404-NOT-FOUND-ERROR/DeskBot`，基线 `0d8d4c5daf261446735b049c6f385981ffd96b21`。

`client-4b1a.patch` 修改原聊天恢复和 outbox 领取筛选；`chat-turn-recovery.mjs` 为恢复记录模块。应用脚本核对基线，支持重复执行：

```powershell
python integrations/client-4b1a/apply.py <客户端目录>
python scripts/verify-client-expression-4b1a.py --client-root <客户端目录> --node <Node24程序路径>
```

测试使用独立 SQLite、固定模型和内存设备。`recovery.test.mjs` 由脚本放在客户端 service 目录，`http-fixture.mjs` 仅用于隔离联测。运行前核对 Python 可导入 `jsonschema / referencing`。

## 接现有工作台

先启动已应用补丁的客户端；工作台进程使用原 USB 所有者。4b1b 增加以下启动参数：

```text
--client-url http://127.0.0.1:<客户端端口>
--client-device-id <本体实际device_id>
--client-ledger <独立提交账本路径>
```

本机 `POST /api/client-presentation/chat` 接收 `event_id / correlation_id / message`，要求 `Content-Type: application/json` 和 `X-DeskBot-Studio: client-presentation-v1`。绑定由本地实际设备状态生成，主人输入只传文字。`GET /api/client-presentation/state` 查看输入接续与命令结果。

首次接入先连接原 USB 并开启原状态与角色查询；收到新鲜目录后再输入。重复提交沿用原事件编号。平静、开心之外的语义明确记录为待扩展；镜像和过渡沿用已安装表达图。

当前完成范围与下一步见[设计及结果](../../docs/client-expression-loop-4b1a.md)。
