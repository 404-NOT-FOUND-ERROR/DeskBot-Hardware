# E1.41 只读容量工具构建报告

更新：2026-10-31。

## 问题与修正

语音代理查询 `self.get_system_info` 时会得到无权限，这是官方 MCP 的预期行为：该工具在 `mcp_server.cc` 中通过 `AddUserOnlyTool` 注册，默认 `tools/list` 会过滤它，只有带 `withUserTools=true` 的用户侧请求才能看到。完整系统信息还包含设备标识和应用摘要，因此没有放宽官方权限。

E1.41 在 ESP-Vocat 的 `DevTools` 中增加普通 AI 可见工具 `self.get_flash_capacity`。它只读返回驱动报告的 Flash 容量，以及运行时分区的 `label/address/size`；明确返回 `mmap_verified=false` 和 `upper_assets_verified=false`。它不返回 MAC、UUID、应用哈希，不写 Flash、NVS 或分区表。

语音端部署后可直接说：

> 查询 Flash 容量和分区布局

不要再说“查询系统信息”，那会继续匹配官方受限工具。返回结果应由客户端按 E1.41 解析器核对；当前接受值是 32 MiB 和旧分区布局。容量查询不等于 CPU 已经能够跨越 16 MiB 访问资源。

## 离线结果

- E1.41 专项测试：9/9 通过。
- `scripts/update-body-source-manifest.py --check`：通过。
- `git diff --check`：通过。
- ESP-IDF 5.5.5、ESP32-S3、N32R16、显式 Octal 32 MiB 隔离构建：通过。
- App：4,478,192 B；构建日志显示最小 App 分区仍有 27% 余量。
- 隔离工程相对 E1.39 构建参考只有 `dev_tools.cc` 和本地依赖路径两处差异。bootloader、分区表、构建参考 assets 和语音模型逐字节一致；App 因新增工具增加 1,248 B。

完整机器报告位于 `artifacts/expression-capacity-tool-e1-41-20261031/report.json`，构建目录位于 `tmp/e141-capacity-tool-20261031/o-32m/`。

## 部署边界

E1.41 已按授权只刷 COM4 本体的 App 分区 `0x10000`，没有刷 COM8/C61、擦除 Flash、写分区表、设备现有 E1.18 assets、语音模型或 NVS。完整回读、启动日志和本体回归见 [E1.41 实机记录](expression-capacity-tool-e1-41-live-20261031.md)。

启动日志里的 `MCP: Add tool: self.get_flash_capacity` 只证明固件注册成功。官方 MCP 的 `self.get_system_info` 仍是 user-only；新工具虽然用普通 `AddTool` 注册，主机侧还必须请求完整分页的 `tools/list`，确认工具名后再用同一 JSON-RPC id 配对 `tools/call`。在实际返回文件取得前，不能把工具“已注册”写成“查询结果已通过”。

实机查询通过后才进入 E1.42：在不改变当前分区和资源的前提下，验证 CPU 跨 16 MiB 的 `esp_flash_read`/mmap、一致性、释放路径和内存峰值。E1.41 的容量返回不能替代这些验证。

## 证据

- [构建报告 JSON](../artifacts/expression-capacity-tool-e1-41-20261031/report.json)
- [E1.40 实机结果](expression-capacity-boot-e1-40-live-20261030.md)
- [开发路线图](roadmap.md)
