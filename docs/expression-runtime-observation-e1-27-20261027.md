# E1.27 离线表情调用快照工作台 / Offline Expression Observation Workbench

E1.27 在 E1.26 的只读 `SetEmotion` 调用观察契约之上，提供一个完全离线的导入与审阅页面：[打开工作台](../artifacts/expression-e1-27-runtime-observation-20261027/index.html)。它接收不超过 64 KiB 的 E1.26 JSON 快照，显示最近记录、语义统计、覆盖/竞争丢弃计数，并把实际观察到的语义高亮到 E1.19 节点图。

页面只读本地文件，不访问串口、网络、C61、NVS 或设备。导入校验拒绝非法 schema、未知字段、非递增序号、超过 24 条记录和过大文件；失败时保留上一份快照。`other` 只显示为未知语义，不猜测它对应的资源或动作。

证据边界仍是 `EmoteDisplay::SetEmotion` 调用入口：快照不能证明 EAF 已解析、LCD 已播放、动作已完成，也不能据此删除资源或修改映射。样例快照是明确标记的离线合成数据，不是设备运行轨迹。

机器报告与样例位于 `artifacts/expression-e1-27-runtime-observation-20261027/`。该工作台阶段报告固定 `deployment_eligible=false`、`flash_authorized=false`、`hardware_accessed=false`、`serial_accessed=false`、`device_written=false`、`nvs_changed=false`，保留其离线证据边界。E1.26 App-only 随后已单独部署并通过用户本体回归，见 [实机记录](expression-e1-26-live-deployment-20261027.md)。下一步保持当前固件，通过只读 `self.expression.observation` 取得真实 JSON 快照再导入本页，不重复刷写。

## 用户口述结果补充

设备已用语音概括最近观察结果：最近 24 条，主要为 `happy`、`embarrassed`、`sleepy`，其余为 `neutral`。用户无法复制设备语音中的 JSON，因此此信息另记为[口述摘要](expression-runtime-observation-e1-27-oral-summary-20261027.md)，不当成逐条设备记录。工作台将四个语义及当前动画映射单独展示并可跳转到节点；语音摘要没有顺序、准确次数和丢弃计数，不导入成虚构的 24 行表格。

当前只足以作语义级审阅。`happy` 和 `embarrassed` 都映射到 `Happy.eaf`，`sleepy` 映射 `sleep.eaf`，`neutral` 映射 `winking.eaf`；这说明两个语义共用资源，不证明加载成功或 LCD 播放成功，也不授权删减或替换资源。页面默认显示本次口述摘要，原始记录区保持空白；合成示例只在用户明确点击按钮后加载。若后续需要精确次数与顺序，应由程序保存原始 MCP JSON，不要求用户逐条转述。
