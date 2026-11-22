# E1.43b-2 基线适配设计

日期：2026-11-07
状态：双配置构建、独立审计与 AI 复核通过；后续[现场准备](expression-e1-43b2-e3c3b-preparation-20261107.md)已完成，映射期间交互待测

## 目标

把三分钟连续映射探针从 E3c-3a 适配到已验收的 E3c-3b。探针关闭版必须与当前 E3c-3b App 完全一致；开启版只额外包含映射探针。

## 当前基线

| 项目 | 固定值 |
| --- | --- |
| 设备 | COM4，本体 ESP32-S3 N32R16，32 MiB Flash、16 MiB PSRAM |
| App | E3c-3b 查询开启版，4,489,232 B，SHA-256 `180aa8cbcc98e3d5b95329175ff2e3f2ee1d35f806b03cbe0778aa2fc2d0f42a` |
| App ELF | `e0941ae3c68528763764f918a62b508fcb990008936d7dd1c69228f283345098` |
| 分区 | `upper-assets-8m`；E1.18 资源继续使用 |
| 构建报告 | `artifacts/display-dma-repair-e3c3b-20261107/firmware-build-report.json`，SHA-256 `1caccb773de444a2184d12ccbdb4fabe2aab301250a3d517ae043f6b55d8eed1` |

## 接口与数据流

1. `load_reference()` 读取 `tmp/e3c3b-display-dma-20261107/p`，核对 1,073 项源清单、固定构建报告、App、ELF 标识和受保护产物；失败时拒绝构建。
2. `build(run_label)` 从该冻结工程创建隔离副本，仅切换 `DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT`，生成 `off`、`on` 镜像和逐步日志；输出构建报告，目录或报告已存在时停止。
3. 独立审计器重新读取报告、源码、日志和产物，确认关闭版与 E3c-3b App 字节相同，开启版只多出探针配置与符号，bootloader、分区表、模型及其他保护产物一致。

数据流：

```mermaid
flowchart LR
    A[E3c-3b 冻结工程与报告] --> B[隔离构建目录]
    B --> C[探针关闭版]
    B --> D[探针开启版]
    C --> E[独立审计]
    D --> E
    E --> F[记录候选与回退镜像]
```

## 依赖与边界

- 使用已锁定的 ESP-IDF 5.5.5、托管组件和 `upper-assets-8m` 配置；构建脚本与 YAML 审计使用固定 IDF Python 3.11。
- 本构建阶段在电脑端完成。后续[专用现场准备](expression-e1-43b2-e3c3b-preparation-20261107.md)已保存实机快照与恢复计划；下一步测试映射期间交互。
- 每轮输出写入新的 `tmp/` 和 `artifacts/` 子目录，不覆盖旧 E3c-3a 证据。

## 验收

- 冻结输入与托管组件清单通过校验。
- 关闭版 App 大小、SHA-256 和嵌入 ELF 标识与 E3c-3b 完全一致。
- 开启版仅增加连续映射探针；无其他诊断探针，App 余量不少于 1 MiB。
- 构建日志、配置、路径标签和受保护产物通过独立审计。
- 测试覆盖报告摘要和版本不符、冻结源码改动、关闭版 App 不匹配、探针配置额外变化、探针符号隔离、受保护产物变化、容量余量不足和日志缺少成功标记。

## 本轮结果

- 新增 17 项构建检查与 51 项相关回归通过；原生探针 22 场景的输入摘要与本轮相同，沿用其通过记录。
- 关闭版 App 为 4,489,232 B，与已验收 E3c-3b 完全一致。
- 开启版为 4,494,096 B，增加 4,864 B；App 剩余 1,649,904 B。两版内部静态数据均为 50,800 B，探针任务栈为 8 KiB。
- 本轮核对 1,073 项源记录、62 个辅助输入和 11,562 个工程文件；配置仅探针开关变化，受保护产物一致。
- [构建报告](../artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107/e143b-e3c3b-20261107-a-build-report.json)、[独立审计](../artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107/e143b-e3c3b-20261107-a-audit.json)和[AI 复核](../artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107/source-review.json)均通过。后续现场快照、恢复计划与执行工具已就绪，映射期间交互待测。

本轮汇总：[验证记录](../artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107/validation.json)。设备继续运行已验收 E3c-3b；本轮为电脑端构建与审计。
