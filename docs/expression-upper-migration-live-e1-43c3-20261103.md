# E1.43c-3：原表情包迁移

更新：2026-11-03。COM4 实机迁移、32 MiB 最终回读和启动检查已完成；本体交互验收待用户完成。

本轮按用户“直接迁移吧”的指令执行。E1.43b 的三分钟连续映射交互测试保留为待测，不计入本轮已通过项目。

## 范围与顺序

1. 核对 COM4 与已验收设备身份，保存 32 MiB 整片备份。
2. 核对现场 App、bootloader、旧资源和模型与已验收基线一致。
3. 保存计划、原分区表扇区和上半区原始字节。
4. 将原 E1.18 包写入 `0x1000000..0x1800000`，完整回读后提交 `0x8000..0x9000` 分区表扇区。
5. 回读活动资源、分区表和静态区域，采集原 App 启动日志，再做屏幕与交互验收。

App、bootloader、NVS、独立模型和旧资源保持原位。新布局容量为 8 MiB；原包 5,538,351 B，剩余 2,850,257 B。29 个成员及 `surprised → shocked.eaf` 保持不变。

## 实机结果

- 写入目标：COM4；资源区 `0x1000000..0x1800000`（8 MiB），分区表扇区 `0x8000..0x9000`（4 KiB）。未写 COM8/C61。
- 资源包写入与 8 MiB 回读通过。资源为 5,538,351 B，29 个成员，剩余 2,850,257 B；包哈希 `8e9dae01b99060c313a76f2dfc7c2b854b80c4584d76f607a74dd2903d39fce4`。
- 新分区表哈希 `407663cb70bc52d7452ee6afea441cc7c0655e616205cefbf21c110ecd8c6689`。整片 32 MiB 最终回读摘要 `d1d19558a8183086c8f4ffa9679d4b7f1dacddea1c9bc834ac7eb18d54d5408c`。
- App、bootloader、NVS、旧资源、模型及其他受保护区域核验通过；NVS 前后摘要一致。
- 原 App 启动，`app_main`、LCD、触摸、PSRAM 和 OPI_STR 初始化标记均出现，无 panic 或资源/模型错误标记。启动日志没有证明上半区资源挂载或 EAF 实际播放。
- 结果：`migration_readback_and_boot_passed_interaction_pending`。设备交互与资源播放待用户验收。
- 专项事务/传输/最终回读主机测试 122 项全部通过。全套件 719 项中 714 通过、2 跳过；3 项既有 E1.26 源码/构建报告检查失败，本次迁移专项没有新增失败。

执行材料：[机器审计](../artifacts/expression-upper-migration-live-e1-43c3-20261103/report.json)、[执行复核](../artifacts/expression-upper-migration-live-e1-43c3-20261103/execution-review.json)。完整备份、逐块读回和启动日志保留在本机 `tmp/e143c-live-20261103-direct1`。

## 执行与恢复

硬件计划使用独立 `hardware_migration` 模式，明确保存本次直接迁移指令。模拟计划仍拒绝原生串口适配器；硬件计划也拒绝模拟设备。写入沿用已测试的事务顺序和恢复日志。

本轮主机测试共 122 项通过，包含 3 项最终回读审计；完整套件 719 项中 714 通过、2 跳过，3 项为此前 E1.26 源码/构建报告检查。详见[主机测试](../artifacts/expression-upper-migration-live-e1-43c3-20261103/host-tests.json)与[完整回归](../artifacts/expression-upper-migration-live-e1-43c3-20261103/full-suite-final.json)。

执行入口：`scripts/run-expression-upper-migration-e1-43c.py`。恢复使用同一入口的 `recover`，读取本轮持久计划与原始字节，不依赖候选资源。恢复时先还原并核对原表，再还原上半区。

## 后续

请先验收屏幕显示、滑出菜单并返回、摸头、点击对话和一轮语音对话；对话中与结束后各切换一次菜单，再测试睡眠/唤醒。确认现有表情能正常显示后，下一阶段只加入一个新表情并在节点工作台预览，检查新包容量及实机播放。E1.43b 三分钟连续映射测试仍需单独安排。
