# E1.41：只读容量工具实机记录

部署日期：2026-10-31。状态：**App-only 已部署，固件注册通过；主机工具返回待核对**。

## 写入与回读

| 项目 | 结果 |
| --- | --- |
| 目标 | COM4，本体 ESP32-S3 v0.2 |
| 物理 Flash | 32 MiB，MXIC `C2:8039`，eFuse Octal，3.3 V |
| 写入范围 | 仅 App `0x10000`，候选 4,478,192 B |
| 实际擦除范围 | `0x10000..0x456000`，4,481,024 B |
| 候选 App SHA-256 | `dd5ce104616bedc5e2a4e5b4d5c3d5685043ea9beba021e727e46fc1403b8e31` |
| 回读 payload | 与候选逐字节一致 |
| 回读尾部 | 2,832 B 全为 `0xFF` |
| 写入校验 | `Hash of data verified` |

证据保存在项目私有 `tmp/e141-capacity-tool-20261031/`：`prewrite-chip-id.log`、`prewrite-flash-id.log`、`write-app-com4.log`、`readback-app.log`、`app-readback-sectors.bin` 和 `boot-after-e141.log`。启动日志 SHA-256 为 `98085131137aa5c4da1d4485a15985e7878336015b580f5535b474dfd2b80487`；回读完整范围 SHA-256 为 `d7e3b8aaadada9f7853a4dc7fc8ab4dcef33bc4166680c1035622e08e3387a44`。

## 启动结果

日志确认 `mxic (opi)` / `flash io: opi_str`，LCD、触摸、EAF 索引、语音模型、Wi-Fi 和 MQTT 均初始化，应用进入 `idle`。同时出现：

```text
MCP: Add tool: self.get_flash_capacity
MCP: Add tool: self.echo_base.events
MCP: Add tool: self.shell.identify
MCP: Add tool: self.shell.list
```

没有发现 panic、assert、brownout、fatal 或重启循环。E1.40 已验收的本体回归边界继续保留；本轮没有写资源、模型、分区表、bootloader、NVS 或 C61。

## 权限状态的准确解释

固件 MCP 有三个不同层次，不能混写：

1. **注册**：启动日志出现 `Add tool`，证明 `DevTools::Initialize` 执行了普通 `AddTool`。
2. **主机可见**：主机必须调用 `tools/list`，处理 `nextCursor`，并在列表中看到 `self.get_flash_capacity`。官方 `self.get_system_info` 通过 `AddUserOnlyTool` 注册，默认列表不包含它；新工具没有这个 user-only 标记。
3. **实际返回**：主机用同一 JSON-RPC id 调用 `tools/call`，并保存原始 text block；只有这一步得到的 JSON 才能交给 E1.41 解析器。

当前已取得第 1 层，尚未取得第 2/3 层的主机证据。用户反馈语音代理仍提示“没有权限”，但这条口述没有说明它实际尝试的工具名，也没有主机错误响应，因此只能记录为主机侧症状，不能归因到本工具。语音说“没有权限”或“查不到”不能反推设备的 `flash_size`；也不能通过串口启动日志伪造工具返回。若主机只请求第一页、缓存旧工具列表或有自己的工具白名单，应在主机侧修复或明确记录阻断原因。

## 下一闸门

先取得真实配对的 `tools/list` 分页和 `tools/call` 返回，并用 `protocol/capacity_query.py` 核对 32 MiB 与旧分区表。之后进入 E1.42：只读验证 CPU 在 `0x01000000` 边界两侧的 `esp_flash_read` 与 `spi_flash_mmap` 一致性、失败和释放路径、重复运行无泄漏，以及内部堆/PSRAM 峰值；不写测试图样，不改当前分区/资源/model/NVS。通过 E1.42 后才制作 8 MiB 资源布局候选。

完整机器结果见 [live-result.json](../artifacts/expression-capacity-tool-e1-41-20261031/live-result.json)，构建结果见 [E1.41 构建报告](expression-capacity-tool-e1-41-20261031.md)。
