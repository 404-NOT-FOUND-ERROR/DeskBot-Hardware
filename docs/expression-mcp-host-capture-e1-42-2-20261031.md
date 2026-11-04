# E1.42.2 MCP 主机捕获闸门

## 目的

E1.42.1 只证明固件注册了普通 `AddTool`，不能证明当前 MCP 主机看到了工具或能调用工具。E1.42.2 定义一份离线、可复核的主机证据文件，要求把同一会话的完整 `tools/list` 分页和目标 `tools/call` 原始响应成对保存。它不打开串口、不访问设备、不修改固件、不改变权限，也不把语音转述当作 MCP 证据。

## 输入格式

输入必须符合 [`mcp-host-visibility-e1-42.schema.json`](../protocol/mcp-host-visibility-e1-42.schema.json)：

- 顶层 `schema` 为 `deskbot.mcp.host_visibility.e1_42/v1`，并记录 UTC 接收时间。
- `list_pages` 保留每个 `tools/list` 请求/响应；第一页不带 `cursor`，后续页必须使用上一页的 `nextCursor`。页边界从当前页未返回的第一项开始，因此页面不应按上一轮错误结论额外去重。
- `tool_calls` 保留同一主机的 `tools/call` 请求/响应，目标为 `self.get_flash_capacity`、`self.probe_flash_addressing`、`self.echo_base.events`、`self.shell.identify` 和 `self.shell.list`。
- 请求和响应的 JSON-RPC `id` 必须配对；重复 JSON 键、错误会话、非法分页、未知目标工具和非空调用参数均拒绝。

## 验证和分类

从仓库根目录运行：

```powershell
& '.\scripts\preflight-deskbot-toolchain.ps1' -Mode Offline
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' .\scripts\validate-mcp-host-e1-42.py `
  --capture .\tmp\mcp-e142-host\capture.json `
  --output .\tmp\mcp-e142-host\report.json `
  --probe-output .\tmp\mcp-e142-host\probe.json
```

报告保留每个成功调用的 `payload_json` 和解析后的 `payload`。`--probe-output` 只导出 `self.probe_flash_addressing` 的嵌套 JSON，并拒绝覆盖已有文件；导出的文件可以直接交给：

```powershell
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' .\scripts\validate-expression-e1-42-probe.py .\tmp\mcp-e142-host\probe.json
```

`self.get_flash_capacity` 的嵌套结果复用 `protocol.capacity_query.validate_expected_capacity`，因此 32 MiB 驱动值、旧分区表、重复标签和边界漂移会在主机报告中明确标为 `capacity_payload_invalid`。`self.probe_flash_addressing` 复用 `protocol.storage_capacity_probe.parse_probe`：结构错误标为 `probe_payload_invalid`，结构正确但 `probe_passed=false` 标为 `probe_failed`。

顶层 JSON-RPC `error` 和 MCP `result.isError=true` 都会保留原始消息，并分类为 `permission_denied`、`unknown_tool`、`invalid_parameters` 或 `rpc_error`。分类是定位线索，不是权限授予；遇到“无权限”仍要先确认工具名、完整分页、主机白名单/缓存和是否误用了官方 user-only `self.get_system_info`。

## 当前状态

离线夹具覆盖完整分页、游标断链、页重叠、重复 JSON 键、缺失目标、顶层错误、`isError=true`、参数错误、容量负载校验、探针负载保留和运行失败分类。专项测试为 **16 项通过**（以当前工作树运行结果为准）。真实主机捕获尚未取得，因此 E1.42 运行闸门仍为 `pending_host_access`；不能把静态注册、设备基础交互或用户口述结果写成工具调用通过。

下一步是从可访问且已授权的官方 MCP 主机取得一份原始捕获。拿到后先运行本页两个校验器，再把报告和原始 JSON 纳入阶段证据；只有容量返回、探针 JSON 对照 E1.40 全片回读、完整分页和同会话调用都通过，才进入 E1.43 的 8 MiB 资源布局设计。

## 安全边界

- 不修改 `AddUserOnlyTool`，不扩权 `self.get_system_info`。
- 不刷写、不擦除 Flash、不改变 NVS，不写 COM8/C61。
- 所有报告和导出文件必须位于仓库 `tmp/`；源代码、回退镜像、用户验收记录和已有构建证据保留。
