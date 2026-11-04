# E1.43a-6 恢复后的实机验收

2026-11-03，用户确认以下操作正常：屏幕显示、滑出菜单并返回、摸头、联网对话，以及对话中和结束后切菜单。

设备基线为 E1.42 App + E1.40 bootloader + E1.18 表情资源，沿用旧分区表。恢复前后的 32 MiB 字节核对和启动检查见 [A6 实验记录](expression-upper-live-e1-43a6-20261103.md)；本次反馈补齐恢复后的交互验收。

A6 探针结果仍为 `probe_failed_but_restored`。下一步是 A7 同轮快照修正与隔离构建，再进行新的单页验证。

结构化记录：[验收 JSON](../artifacts/expression-upper-live-e1-43a6-20261103/acceptance-20261103.json)。
