# E1.25 表情运行时观测设计

E1.24 已确认静态源码路由不能说明设备实际收到或播放了哪些表情。E1.25 只定义下一版可选的被动观测契约，并提供纯主机回放模型；它**没有实现固件观察器**。当前设备映射保持用户选择的 `surprised → shocked.eaf`，无固件构建、串口访问或设备写入。

## 观测点与证据边界

本体使用的窄观测点是 `firmware/body/main/display/emote_display.cc` 中的 `EmoteDisplay::SetEmotion(const char*)`。静态源码显示，LLM 的 emotion 字段由 `Application` 调度后调用该方法；底座配对回调也会直接调用它。观测器以后若实现，应在此方法入口附近记录语义调用，不得改变后续 EAF 播放调用或官方动作映射。当前源码没有证明这些调用在所有时刻都串行，所以未来实现必须按多生产者并发审查。

记录的唯一事实是：某个表情语义调用到达了这个方法。它不证明表情资源存在、资源解析成功、播放器启动、LCD 实际呈现或底座动作完成。上述阶段必须分别增加独立证据，不能从 `SetEmotion` 反推。

## 记录契约

JSON Schema 位于 [`protocol/expression-runtime-observation-e1-25.schema.json`](../protocol/expression-runtime-observation-e1-25.schema.json)，主机回放模型位于 [`protocol/expression_runtime_observation.py`](../protocol/expression_runtime_observation.py)。

| 字段/规则 | 契约 |
| --- | --- |
| 观测点 | `EmoteDisplay::SetEmotion`；不记录 STT、LLM 提示/回复、TTS 音频或原始协议帧 |
| 事件名 | 每条记录固定为 `observation=set_emotion_call_entered`，快照同时标记 `claim_scope=set_emotion_call_only` |
| 表情值 | 29 个静态清单语义之一；空值忽略；未知、非 ASCII 或超过 24 字节的名称一律折叠为 `other`，不保存原字符串 |
| 时间 | 自启动以来的 `uint32` 毫秒数；重复窗口用无符号差值，测试覆盖计时器回绕 |
| 序号 | 本次启动内从 1 递增的 JSON 安全整数，最大 `2^53−1`；不跨启动比较、不写入持久存储。耗尽后停止收新记录并增加饱和丢弃计数 |
| 重复合并 | 仅当新调用与最后记录语义相同，且距离最后一次调用不超过 250 ms 时合并；`repeat_count` 饱和于 65,535，记录首/末时间都保留 |
| 容量 | 最近 24 条记录；新记录覆盖最旧记录并递增 `records_overwritten` |
| 丢弃 | 未来固件必须用固定内存、常数上界、零等待的并发准入；队列满或一次准入竞争失败即丢弃并计数，绝不等待/重试 |
| 生命周期 | RAM 易失；重启清空；不写 NVS/Flash；关闭后观测入口必须立即返回且不分配、不记日志、不改变后续处理 |
| 读取/清除 | 未来只有显式只读快照才导出；不自动上传/导出。清除是显式操作，清记录和丢弃计数但保留本次启动的序号；当前没有查询或清除 UI/API |

`records_dropped_contention` 和 `records_dropped_sequence_exhausted` 用于说明观测覆盖缺口。它们不能计入完整的调用频率；报告必须保留被覆盖/丢弃计数，不能把有限环形缓冲描述成完整历史。

### 并发闸门

目前至少可见应用调度路径和底座 UART 回调路径，因此不能假设一个生产者。固件实现前需检查实际回调上下文及并行访问。可接受的实现必须以固定内存进行一次有界、非等待的入队/准入尝试；竞争或满载时丢弃并计数。禁止在观测路径获取可能等待的 UI/播放器互斥锁、调用 LVGL/播放器/动作函数、分配内存、格式化/输出日志或发网络消息。若目标 IDF/芯片上无法证明该原语满足有界无等待要求，则本阶段不接入钩子。

主机模型用于回放序列规则，不验证 FreeRTOS、SMP 原子性、静态内存占用或调用上下文。后续固件实现必须另做源码并发审查和构建，不能把本机测试称作线程安全证明。

## 隐私与功能边界

- 不保存语音文字、模型提示/回复、用户/设备身份、MAC、网络状态、时间墙钟、原始图像/帧缓冲、EAF 字节或 UART 帧。
- 不记录“资源已加载”“已渲染”“动作已完成”等未观测事实。
- 不改变 `SetEmotion` 已有播放器调用、动作映射、调度、UI、DOA、摸头、音频、底座和资源分区。
- 默认关闭；无 NVS 持久化；当前无自动导出、通知、设置页、MCP 工具或客户端传输。
- 不使用这份设计、回放数据或无观测结果来删除/合并资源或替换 `shocked.eaf`。

## 离线验收与后续闸门

E1.25 离线验收覆盖：默认关闭、有限记录、相同语义合并/窗口边界、不同语义打断、计时器回绕、未知/超长名称脱敏、最大序号、清除语义、schema 字段限制和超容量拒绝。机器报告在 [`artifacts/expression-e1-25-runtime-observation-20261026/report.json`](../artifacts/expression-e1-25-runtime-observation-20261026/report.json)。

此阶段通过只表示**设计与主机契约通过**，不表示设备已采集。下一步需单独审阅是否值得加最小固件只读观察器；若批准，再新增独立默认关闭的配置项和显式读取接口，只构建、不刷写，先验证关闭路径与启动/菜单/对话回归。实机采集另需用户确认设备连接和刷写范围；任何表达映射清理仍要依赖单独的资源级证据与视觉验收。

复现命令：

```powershell
python scripts/audit-expression-e1-25-observation.py
python -m unittest tests/test-expression-e1-25-observation.py
```

报告必须保持 `deployment_eligible=false`、`flash_authorized=false`。E1.25 不构建固件、不连接串口、不访问或修改设备/NVS。
