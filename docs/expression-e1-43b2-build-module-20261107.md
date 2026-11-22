# E1.43b-2 当前基线双配置构建

日期：2026-11-07
结果：当前 E3c-3b 探针双配置构建、独立审计与 AI 复核通过；按[适配设计](expression-e1-43b2-current-baseline-design-20261107.md)执行。

## 当前构建：E3c-3b

入口为 `scripts/build-expression-e1-43b-e3c3b.py`，独立审计为 `scripts/audit-expression-e1-43b-e3c3b-build.py`，均使用固定 IDF Python 3.11。[本轮报告](../artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107/e143b-e3c3b-20261107-a-build-report.json)记录完整输入与日志。

| 项目 | 关闭版 | 开启版 |
| --- | --- | --- |
| App | 4,489,232 B，与当前稳定版完全一致 | 4,494,096 B，增加 4,864 B |
| App 余量 | 1,654,768 B | 1,649,904 B |
| 内部静态数据 | 50,800 B | 50,800 B |
| 额外配置 | 无 | 仅连续映射探针 |

新增 17 项检查与 51 项相关回归通过；原生 22 场景的输入摘要与本轮对应。冻结输入与完整组件在构建前后均已核对；唯一托管组件替换为已验收的 ST77916 精确补丁。[独立审计](../artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107/e143b-e3c3b-20261107-a-audit.json)和[AI 复核](../artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107/source-review.json)通过，保护产物一致。后续适配现场快照与 App 恢复计划，再做映射期间交互。

## 历史构建：E3c-3a

在当轮 E3c-3a 查询开启 App 上，只切换连续映射探针开关，分别构建关闭版和开启版。关闭版与当轮 E3c-3a App 完全一致；开启版只增加探针。

## 接口与数据流

1. `scripts/build-expression-e1-43b-e3c3a.py` 核对冻结工程、托管组件及完整输入清单，再复制到独立运行目录。
2. 同一源码分别构建 `off` 和 `on`，保存配置、产物、日志和摘要。
3. `scripts/audit-expression-e1-43b-e3c3a-build.py` 独立重读输入与两轮产物，核对日志、路径标签、符号、容量和受保护文件。

构建记录为 [off-on-20261107-b](../artifacts/expression-continuous-mmap-e1-43b2-e3c3a-20261107/off-on-20261107-b-build-report.json)，独立审计为 [audit](../artifacts/expression-continuous-mmap-e1-43b2-e3c3a-20261107/off-on-20261107-b-audit.json)。本轮清单检查 1,072 个源码记录、62 个辅助输入，共 11,562 个工程文件；62 个辅助输入摘要为 `28cd75cdb743c04590d66537ebb2eec8d629a32e5354667443af412cb5ce2faa`。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| 构建门禁 | 18 项通过，包含未列文件、重解析点、错误日志、布尔返回码和运行标签错配 |
| 当前 E3c-3a 基线 | 14 项通过 |
| 连续映射主机逻辑 | 23 项通过 |
| 事务适配边界 | 16 项通过 |
| 原生 C++ 探针 | 22 个场景通过，使用 Zig C++17 编译器 |
| 关闭版 App | 4,489,232 B；SHA-256 与 E3c-3a 完全相同；余量 1,654,768 B |
| 开启版 App | 4,494,096 B；探针增加 4,864 B；余量 1,649,904 B |
| 受保护产物 | bootloader、分区表、模型、OTA 数据、刷写参数及 bootloader 配置均匹配 |
| 独立审计 | 通过；关闭版无探针符号，开启版有探针符号；无其他探针符号 |

工具链预检确认 ESP-IDF 5.5.5、IDF Python 3.11.2、CMake 3.30.2、Ninja 1.12.1 和 Xtensa GCC 14.2.0。构建门禁测试与构建脚本使用 IDF Python；不依赖 YAML 的主机测试使用 Offline Python 3.13.0。

## 复核与后续

独立 AI 对构建入口、审计器、测试和设计复核通过，没有遗留问题。复核期间要求增加报告、轮次和日志的 `run_label` 错配测试，现已加入并通过。

首次启动构建时发现辅助模块函数归属调用错误，尚未运行 ESP-IDF。修正为调用模块顶层 `run()` 与组件 `HELPER` 后，以新的 `off-on-20261107-b` 标签完成构建；前次运行目录保留作为记录。架构指南已加入“先核对实际辅助函数定义，再启动长构建”的操作项。

本构建随后已完成自动映射和恢复，见[执行记录](expression-e1-43b2-execution-module-20261107.md)。下一轮先适配 E3c-3b，再生成同轮 App 恢复计划并测试映射期间交互。
