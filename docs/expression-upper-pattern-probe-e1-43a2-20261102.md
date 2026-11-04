# E1.43a-2：固定范围 CPU 只读探针与隔离构建

本阶段只完成源码、离线测试和双配置构建审计。没有打开串口或写入设备；测试图样仍未部署。报告与构建证据分别保存在 [机器报告](../artifacts/expression-upper-pattern-probe-e1-43a2-20261102/final-build-report.json) 和被忽略的 `tmp/e143a-probe-20261102/evidence/final/`。

## 结果

- 固件探针固定访问 `0x01000000..0x01010000`，按 1 KiB 分块计算 direct-read 与 mmap SHA-256；逐扇区校验 16 个图样标记，读取跨 16 MiB 边界样本，并记录映射释放、MMU 页数与内部堆/PSRAM 快照。任务只在诊断构建中存在，默认关闭，执行一次后退出；没有 UI、网络监听、任意地址参数或 Flash 写入接口。
- 最终源码差异范围为 `Kconfig.projbuild`、`dev_tools.cc`、`upper_pattern_probe.h` 三个文件；构建器在启动和完成阶段均校验输入哈希及依赖锁。
- `off` App 为 4,481,776 B，分区余量 1,662,224 B。它相对锁定的 E1.42 App 只有 65 个字节变化，全部落在 ELF 身份、校验和与末尾摘要范围。
- `on` App 为 4,486,768 B，分区余量 1,657,232 B；新诊断任务符号只在此配置存在，旧 E1.42.3 诊断任务符号不存在。
- bootloader、分区表、OTA 初始数据、构建参考 assets 与语音模型在两种配置中均与锁定基线逐字节摘要一致；两份生成配置中仅 App 诊断开关按预期变化。
- 最终构建：`scripts/build-expression-e1-43a-probe.py --run-label final`，ESP-IDF 5.5.5、ESP32-S3 工具链、隔离 `tmp/`。ESP-IDF 环境标记为 `v5.5.5-dirty`；日志含缺少 `ESP_ROM_ELF_DIR` 的 GDB 初始化警告，但两种构建均成功完成且报告通过。

此前 `first` 轮构建期间源文件发生变更，生成的镜像不作为验收依据；保留中间日志与快照，最终 `final` 轮重新固定源码并完整跑两种配置，只有后者的完成报告用于下一阶段。

## 测试与边界

- `tests.test_storage_upper_pattern` 与 `tests.test_storage_upper_pattern_runtime`：34 项通过。
- `prepare-expression-e1-43a-pattern.py --run-label initial --check`：通过，图样摘要为 `63cd7bb2d9acd763bec3d74f3561ac68572f3f45fdfc8c4fc6513d7f6048df22`。
- 仓库可移植套件：441 项运行，2 项因本机证据缺失跳过；3 项 E1.26 历史冻结检查失败/报错。失败来自旧阶段固定源码锚点遇到后续 Kconfig 扩展，以及本机私有 E1.26 构建报告不再匹配当前源码清单；没有放宽这些历史断言或重写旧报告。
- `git diff --check` 通过。构建产物不是部署镜像；构建器明确记录 `device_written=false`、`runtime_verified=false`、`hardware_accepted=false`、`partition_table_changed=false`。

本阶段证明固定范围测试固件可构建、默认关闭配置的可执行内容与锁定 E1.42 一致，不证明上半区真实 CPU 读取、地址别名排除或 8 MiB 连续 mmap；这些仍需随后单独实机门禁。

## 下一阶段

E1.43a-3 先取得新鲜的 COM4 芯片身份、分区表、32 MiB 全片读取和目标页完整擦除扇区回退数据，独立核对大小与摘要后才可考虑写入已知图样。单页试验须复核保护区、回读图样、运行时 CPU 报告，然后精确恢复原目标页与原 App 并回读；恢复后再做本体人工回归。任一身份、备份、页摘要或回退门禁不符即停止，不刷写 C61、正式分区表或 NVS，也不因此开始资源迁移。
