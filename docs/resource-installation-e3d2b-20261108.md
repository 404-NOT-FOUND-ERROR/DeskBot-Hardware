# E3d-2b 害羞资源安装：接续设计

更新：2026-11-09 · 状态：离线计划与 62 项测试通过；36 项目录、平静回执与日常交互复测通过，平静画面稍后确认。设备仍使用 E1.18。

目标：沿用当前 E3d-2b，将 `embarrassed` 从 `Happy.eaf` 接到 `shy_candidate.eaf`，25 fps、循环播放。工作台继续显示替换前后节点和实际安装目录。

## 当前结果

[11 项输入核对](../artifacts/resource-installation-e3d2b-20261108/offline-readiness.json)通过：当前 App、分区表、E1.18 原资源、E1.45 候选、包边界和容量均匹配。候选包为 5,981,260 B，8 MiB 分区剩余 2,407,348 B。

新入口 `scripts/deploy-e3d2b-resources.py` 固定使用 E3d-2b；旧入口继续使用 E3c-3b。两套计划与适配器交叉检查版本，两版共用事务、日志和资源区传输代码。

实现沿用同一事务核心：旧入口默认 `E3c-3b`，新入口固定 `E3d-2b`。新计划使用 `deskbot.resource_installation_plan.e3d2b/v1`，增加 `baseline: E3d-2b`；其余字段保持 `mode / offset / bytes / identity_sha256 / snapshot_sha256 / rollback_sha256 / candidate_sha256`。计划摘要覆盖所有字段，两个入口分别核对版本。恢复只依赖同轮快照、原资源、计划及日志。

资源适配器沿用 `ResourceDevice` 的限额传输。E3d-2b 子类核对固定版本的正常启动、周期 hello、设备 MAC 派生身份及启动内的序号；安装和恢复均核对当前 ELF。离线和检查入口延迟加载串口模块。

首次保存写入意图失败时不写 Flash；执行器重启原 App，并报告日志错误。若启动失败或抛错，会同时报告启动未确认和原始保存错误。恢复过程遇到日志保存失败时保留上一份持久状态，存储恢复后继续重试。

## 接口、数据流与依赖

| 接口 | 输入 | 输出 |
| --- | --- | --- |
| `prepare-offline / check` | 已保存快照、固定 E1.45 候选、E3d-2b 身份 | 版本、范围、容量和恢复材料核对 |
| `prepare-live` | 当轮 COM4 身份和新鲜快照 | 绑定当前版本的安装计划和原资源备份 |
| `install` | 同轮计划 | 写入资源、完整回读、当前 App 启动记录 |
| `recover` | 同轮原资源、计划和日志 | 恢复原资源并启动当前 App |

数据流：核对 E3d-2b → 保存原资源 → 写 E1.45 → 核对其余区域与启动 → 工作台重读新目录 → 验收害羞动画和日常交互。

当前电脑端只开放 `neutral`。害羞表情的实机验收需明确可重复的 `embarrassed` 触发入口，并沿用原应用调度；该接口与安装适配分别测试。

唯一资源范围为 `[0x1000000, 0x1800000)`。App 身份固定为 SHA-256 `b7f7139d688c46f10e5662387cc74c87a7614d289b3364872a48301c93d59532`，ELF 为 `87d3b429846407fa26ad7b38dbe5c1a54fea0d1cbd5f30bf5bafa01429a4cc7a`。复用现有唯一串口所有者、事务日志、esptool 传输和启动身份解析。安装后按 E1.45 目录核对，原 E1.18 目录用于恢复核对。

## 验证与下一步

[62 项测试](../artifacts/resource-installation-e3d2b-20261108/validation-4.json)通过：20 项旧入口回归，42 项新版单元与生产适配器模拟。覆盖双向版本隔离、完整安装/恢复、部分写入、回读或启动失败、恢复重试、NVS 保留、候选删除后的恢复，以及首次意图保存失败时原 App 启动成功与失败两种结果。

实现、结果和路线由另一位 AI 复核，见[复核记录](../artifacts/resource-installation-e3d2b-20261108/peer-review.json)；共享代码的新旧摘要见[来源补充](../artifacts/resource-installation-e3d2b-20261108/source-review.json)。

离线计划位于 `tmp/e145-assets-20261108-e3d2b-offline1/`，准备与检查一致；[计划结果](../artifacts/resource-installation-e3d2b-20261108/offline-preparation.json)记录版本、8 MiB 范围和两个资源摘要。设备安装使用当轮新建的 live 计划。

接下来：

1. 当前 E3d-2b 的目录、平静回执与日常交互复测通过；主人回家后确认平静画面。
2. `embarrassed` 触发的设计、构建与离线测试完成，按[顺序演练](studio-shy-deployment-e3f1.md)接续现场准备。
3. 采集当轮身份和快照，在 E3d-2b 上安装 E1.45，再重读设备目录。
4. 基于新资源快照接入害羞触发增量，验收动画、菜单、对话、摸头和睡眠唤醒。

离线准备引用已保存的快照；实机计划使用当轮现场数据。普通验收由主人方便时操作并统一反馈。

随后：角色表达映射 → 更多表情与已有动作 → 外壳与角色绑定 → 客户端 DeepSeek/TTS。
