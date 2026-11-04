# E1.43a-5：COM4 主机适配器与实机前门禁

**状态：离线适配器与事务集成审计通过；实机写入、CPU 探针和恢复验收仍未执行。** 本阶段只修正并测试 `esptool` 主机边界，没有打开串口、复位设备、构建固件或写 Flash。

## 本阶段完成

- `protocol/storage_upper_experiment_esptool.py` 现在只接受已审查的本体端口 `COM4`，并在每次受影响读写前重新核对 ESP32-S3、32 MiB Flash、16 MiB PSRAM、Octal、无 Secure Boot/Flash Encryption 及计划绑定的现场身份摘要。
- esptool 读取按 4 KiB 扇区向上对齐，返回给事务控制器时截回请求长度；因此候选 App 的 `4,486,768 B` 不会被错误地当成非法几何，也不会读少最后一个扇区。
- 保护摘要覆盖 App 擦除并集、旧表/NVS/PHY、assets、model、两段间隙及上半区测试页之外的全部 32 MiB。读取和写入范围仍只允许完整测试页或计划绑定的候选/回退 App。
- 写入默认关闭。启用时必须在终端逐字输入包含完整 `plan_id` 的确认短语；该确认是一次明确的操作员动作，`write_enabled` 不能在对象创建后被赋值来绕过。写入前还要验证工作树 `tmp/<run-label>/plan/` 中的回退材料完整且哈希一致，并读取同一 run 下 `recovery.json`；只有该计划已持久记录 `restore_required` 之后的有效阶段才可写入。固定启动捕获入口 `tools/capture-body-boot.py` 的 SHA-256 为 `be6b5b7e2132cdb38cd54e42263b854d424f556ad3f5f8272b1dad18050669f8`，探针和原 App 启动统一使用 `--usb-reset`。没有任意 shell 命令或用户传入的探针/启动命令注入点。
- App 写入使用 `--after no_reset`，由捕获入口明确 USB reset 后再运行诊断 App；恢复后的原始 App 在 Flash 回读和静态保护验证完成后单独启动。避免在下载模式和正常 App 之间把一次复位误当成启动证据。
- 回读日志、分块文件、写入 payload 和串口捕获均只写到本工作树 `tmp/`，拒绝覆盖已有证据；测试夹具使用合成 MAC，不发布真实设备身份。

## 离线证据

- `tests/test_storage_upper_experiment_esptool.py`：**21/21 通过**，覆盖 COM4 锁定、合成身份、交互式计划确认、恢复输入持久化/哈希、恢复日志阶段门禁、扇区对齐读回、完整保护范围、写前新鲜身份、固定 USB reset 捕获、正常启动标记、panic 拒绝、危险 esptool 参数、证据覆盖保护，以及与事务控制器的端到端模拟恢复。新增测试确认适配器进程重启后从同一 run 目录的 `logs`、`captures`、`payloads` 直接文件续号，既有证据字节不变，嵌套目录不参与扫描；命令日志或串口输出文件冲突会在 esptool/USB reset 调用前拒绝。
- `tests/test_storage_upper_experiment.py`：**19/19 通过**。恢复控制器的日志 v3、进程中断恢复、身份/保护区漂移和恢复后启动顺序保持通过。
- 四个相关 Python 文件 `py_compile` 通过，`git diff --check` 对本阶段代码修改通过。

本轮复核后，`tests/test_storage_upper_experiment_esptool.py` 为 **21/21**，`tests/test_storage_upper_experiment.py` 为 **19/19**；四个 Python 源/测试文件通过 `py_compile`。完整可移植套件运行 489 项、2 项跳过；3 项 E1.26 历史冻结检查因后续 Kconfig 漂移和过期私有构建报告仍未通过，保留原断言和报告。发布检查通过 1,587 个文件；工作台检查通过 24 页、23 个 JavaScript 脚本和 23 个 JSON payload；`git diff --check` 通过，仅输出既有换行风格提示。

这些结果只证明主机边界和模拟事务规则，不证明 COM4 当前仍连接目标本体，也不证明 esptool、USB reset、临时探针 App、Flash 回读或 CPU 访问在真实设备上成功。交互式确认不替代设备身份和新鲜回退审核；任何真实写入前仍须逐项完成现场门禁。

## 尚未取得的证据

`offline_passed=true`；`build_passed=pending`；`flash_verified=pending`；`boot_observed=pending`；`tool_verified=pending`；`hardware_accepted=pending`。本阶段没有刷写授权，也没有改变 E1.42 App、E1.40 bootloader、E1.18 assets、分区表、模型、C61 或 NVS。

## 下一道门禁

设备再次就位后，必须使用唯一的新 run label，并按以下顺序执行；开始实机写入前仍须确认当前这次实验的操作范围：

1. 运行 Flash 工具链预检，枚举并确认 COM4 的新鲜 USB 身份；重新读取 `flash_id`、`get_security_info`、App 擦除并集、64 KiB 测试页及全部保护摘要。
2. 独立核对现场回退文件、候选 App、固定图样和计划哈希；任何容量、MAC、安全状态、分区或保护摘要变化都停止。
3. 在持久化 `restore_required` 成功后写入单页图样，完整回读；再写入临时探针 App，USB reset 后捕获固定范围 direct-read/mmap 结果。
4. 无论探针通过或失败，都恢复完整 App 擦除并集和测试页，逐字节回读，再 USB reset 捕获原始 App 正常启动；最后由用户验收本体交互。

恢复、启动和人工验收全部通过后，才允许进入 E1.43b 的 8 MiB 连续 mmap；不把本阶段的离线适配器测试当成容量扩展或资源迁移依据。
