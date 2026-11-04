# E1.29：MCP 表情观测导出契约

E1.29 定义了将一次电脑端 MCP 工具调用保存为 E1.27 可导入快照的离线格式和转换器。当前小智固件以 `self.expression.observation` 返回 JSON 文本；MCP 服务器将其放进一个 `text` 内容块，再通过带 `type: "mcp"` 的网络消息返回。固件没有把完整工具响应写入普通 USB 日志，因而不能仅靠 COM4 日志恢复逐条记录。

契约文件是 [E1.29 capture schema](../protocol/expression-mcp-capture-e1-29.schema.json)，转换器为 `scripts/export-expression-e1-29.py`。它要求由主机同时捕获 `tools/call` 请求和对应响应，核对工具名、JSON-RPC id、成功状态与唯一文本块，再严格校验嵌在文本里的 E1.26 快照。仅有一句语音回答、无配对请求的 MCP 响应，或 `busy`/错误响应都不能导出成运行记录。

[离线链路工作台](../artifacts/expression-e1-29-mcp-capture-20261028/index.html) 使用明确标注的合成样本展示五层结构：主机配对捕获、MCP envelope、JSON-RPC、原始 text 内容块、E1.26 快照及来源证明。节点间切换时显示对应层的请求/响应；text 节点保留嵌套 JSON 字符串，避免把传输内容误看成设备直接返回的对象。工作台不会联网，也不读取设备。

## 导出文件格式

主机采集器将一对请求/响应封装为：

```json
{
  "schema": "deskbot.expression.mcp_capture_input.e1_29/v1",
  "host_received_at_utc": "2026-10-28T04:05:06.123Z",
  "request_message": {
    "type": "mcp",
    "payload": {
      "jsonrpc": "2.0",
      "id": 17,
      "method": "tools/call",
      "params": {"name": "self.expression.observation", "arguments": {}}
    }
  },
  "response_message": {
    "type": "mcp",
    "payload": {
      "jsonrpc": "2.0",
      "id": 17,
      "result": {
        "content": [{"type": "text", "text": "{...E1.26 snapshot JSON...}"}],
        "isError": false
      }
    }
  }
}
```

采集文件可以包含外层 `session_id`，但导出器不把它复制到结果。请求和响应必须使用相同 ID；调用必须是无参数的 `self.expression.observation`。`host_received_at_utc` 仅是电脑接收响应的时间，不覆盖固件 snapshot 的 uptime 字段。

## 运行

将主机端成对捕获的文件放到项目 `tmp/` 下，再从仓库目录执行：

```powershell
python scripts/export-expression-e1-29.py `
  --capture .\tmp\e1-29-capture.json `
  --out-dir .\tmp\e1-29-export
```

导出目录只生成：

- `observation-snapshot.json`：原样保留 E1.26 语义字段，可直接在 [E1.27 观察工作台](../artifacts/expression-e1-27-runtime-observation-20261027/index.html) 使用“导入 JSON 快照”。
- `provenance.json`：工具名、请求 ID、主机接收时间、输入捕获文件 SHA-256、快照 SHA-256、序号范围和丢弃计数，以及明确的证据边界。

输出目录必须是仓库的 `tmp/` 子目录且事先不存在。转换器不覆写结果，不复制原始 MCP 消息、session id 或本机输入路径。输入捕获保留与否由主机采集者管理；manifest 的 hash 用于之后与原输入比对，不包含原始内容。

## 严格失败条件

- 工具名、方法、JSON-RPC 版本或请求/响应 ID 不匹配；有 JSON-RPC 错误、MCP `isError`、多块内容或非文本内容。
- 文本不是单个合法 E1.26 JSON；含重复键、未知字段、未知语义、错误序号、超过 24 条或越界计数。
- 观察器忙碌返回 `{"ok":false,"error":"busy"...}`。这种响应应在设备仍在线时重新查询，不得伪装为空快照。
- 捕获超过 128 KiB、快照文本超过 64 KiB，或输出路径不在仓库 `tmp/`。

## 当前边界与下一道闸门

此阶段完成格式、离线转换器和模拟请求/响应可视化。14 项 E1.29 单元测试通过；Edge/Playwright 检查节点选择、请求/响应切换、嵌套 text、窄屏、E1.27 导入和无网络请求；生成器一致性、13 页工作台检查及仓库全量检查结果以本轮验证记录为准。没有访问设备、调用 MCP、保存真实快照或改动固件/网络客户端。现有语音通道只给出了最近 24 条的大致语义分布，不能从口述恢复序号、精确次数和时间。

下一步是在明确可访问且授权的客户端/服务端 MCP 主机中增加配对 capture 导出点，先输出到项目 `tmp/`，再用此转换器验证并导入真实结果到 E1.27。需保留 JSON-RPC id 和接收时间，避免记录 session id 到规范化输出，并在主机侧确认不会改变工具调用行为。拿到真实文件前，不把合成样本或语音摘要当逐条证据，也不从 COM4、TLS 流量或未知日志旁路采集。
