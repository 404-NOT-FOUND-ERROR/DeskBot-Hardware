# E1.43b-2：迁移后的连续映射准备

当前版本正常，用户选择稍后实测。本阶段完成 `upper-assets-8m` 的离线准备，设备继续使用 E1.42 App、E1.40 bootloader 和 E1.18 资源。

## 本次调整

- 新准备入口核对 E1.43c-3 的保存计划、32 个回读块、App、分区表、资源和模型。
- 容量校验精确匹配新表：活动资源在 `0x01000000..0x01800000`，旧资源保留为 `assets_prev`。
- 连续映射覆盖完整 8 MiB。IDF 可以共享播放器已有的映射，因此页数消耗为 0 或 128；分块哈希、内存下限、三分钟持有和释放检查继续执行。
- 默认关闭版与诊断启用版分别构建。实测只临时写 App，结束后恢复本轮保存的 App 原字节，核对其余区域和正常启动。

官方资源加载、UI、音频、摸头和动作代码沿用当前版本。

共享映射的依据是本体 `assets.cc` 已映射整个活动分区，IDF 5.5.5 的 `flash_mmap.c` 使用 `ESP_MMU_MMAP_FLAG_PADDR_SHARED`。诊断因此记录实际页数变化，再核对完整内容。

## 离线结果

| 检查 | 结果 |
| --- | --- |
| 专项测试 | 99/99 通过，包含新运行器的 App 写入边界和独立恢复 |
| C++ 探针 | 22 个场景通过，含共享映射、错误读取、内存不足及释放检查 |
| 双配置构建与独立复核 | 1,062 个源码项、每版 9 个产物核对通过 |
| 默认关闭版 | App 4,481,776 B，余量 1,662,224 B；与 E1.42 仅 ELF 标识、校验和摘要字节不同 |
| 诊断启用版 | App 4,486,688 B，余量 1,657,312 B；其他保护产物匹配 |
| 迁移材料 | 32 个保存回读块、分区表、资源、模型与原 App 校验通过 |
| 本轮设备操作 | 未打开串口、复位或刷写；三分钟实测稍后安排 |

完整套件为 741 项：734 通过、2 跳过、5 项未通过。三项属于 E1.26 冻结源码/报告检查；两项属于旧迁移预审的源码清单检查。本轮仅更新诊断文件及其清单，旧检查点中的原文件与原摘要一致，见[版本差异核对](../artifacts/expression-continuous-mmap-upper-e1-43b2-20261104/frozen-input-review.json)。全套结果仍保留为未全部通过。

当前[准备报告](../artifacts/expression-continuous-mmap-upper-e1-43b2-20261104/readiness.json)、[构建复核](../artifacts/expression-continuous-mmap-upper-e1-43b2-20261104/build-verification.json)和[部署范围](../artifacts/expression-continuous-mmap-upper-e1-43b2-20261104/deployment-preparation.json)已保存。仓库发布检查和 `git diff --check` 通过。

## 操作入口

按[工具链指南](architecture.md#工具链环境行动指南)选择解释器，从实际 Git 工作树执行。

| 工作 | 脚本 | 环境 |
| --- | --- | --- |
| 双配置构建 | `build-expression-e1-43b-probe.py --run-label upper-shared-20261104` | IDF Python |
| 归档产物复核 | `audit-expression-e1-43b-build.py` | IDF Python |
| 当前布局准备 | `prepare-expression-e1-43b-upper-deployment.py` | IDF Python |
| 实测与恢复 | `run-expression-continuous-mmap-upper-e1-43b.py execute/recover` | IDF Python，实测时使用 |
| 日志与专项测试 | `validate-expression-e1-43b-probe.py`、三个 `test_storage_continuous_mmap*.py` | Offline Python |

旧准备脚本、运行器和报告保留为迁移前记录。当前执行入口均带 `upper`，使用新表和本轮构建。

本轮构建沿用 E1.42 对照配置，生成的分区表用于比较，部署只取 App。App 运行时读取设备上的 8 MiB 资源表；后续正式发布配置也统一为该表。

## 实测时的顺序

1. 核对 COM4 身份和当前镜像，保存新鲜 32 MiB 备份及 App 擦除范围。
2. 临时启动诊断 App，安静等待八个内容块校验。
3. 保持映射三分钟，操作对话、对话中及结束后切菜单、摸头、睡眠与唤醒。
4. 自动恢复原 App，逐字节核对 App，并比较分区表、资源、模型和其他静态区域。

实测时从仓库根运行，使用未占用的标签：

```powershell
& 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' scripts/run-expression-continuous-mmap-upper-e1-43b.py execute --run-label e143b-upper-live-01 --build-report artifacts/expression-continuous-mmap-e1-43b-20261103/upper-shared-20261104-build-report.json
```

本轮候选的 App 擦除范围为 `0x10000..0x458000`。运行器先重新备份，再执行临时 App 和自动恢复。中断后沿用同一标签恢复：

```powershell
& 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' scripts/run-expression-continuous-mmap-upper-e1-43b.py recover --run-label e143b-upper-live-01
```

之后验证一个新表情：节点前后预览 → 实际资源包容量 → 官方播放器检查 → 资源部署 → 屏幕与菜单回归。随后统一正式发布配置，接表达目录和客户端角色绑定；底座修复后继续外壳识别和持久登记。

本轮报告放在 [E1.43b-2](../artifacts/expression-continuous-mmap-upper-e1-43b2-20261104/)。实机映射与交互结果待下一轮取得。
