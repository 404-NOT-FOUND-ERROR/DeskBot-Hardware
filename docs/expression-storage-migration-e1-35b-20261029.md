# E1.35b 原资源迁移与回退审计

更新：2026-10-29。本阶段只生成和审计离线迁移参考，不访问串口、不读取设备、不修改正式分区表、不刷写。

## 结果

E1.35b 将已经验收的 E1.18 资源包按旧布局和 8 MiB 候选布局分别补齐为完整分区镜像，并用版本化 manifest、JSON Schema、逐成员 SHA-256 清单和 0xFF 填充检查锁定来源。候选布局仍只改变 `assets` 容量和 `model` 起点；App、NVS、OTA 数据、PHY 初始化和官方资源成员保持原样。

这批镜像分为两种用途：旧布局镜像和 E1.35a 构建出的 model 是**离线构建参考**；它们不等于当前设备的精确回读。只有 E1.35c 从本体实际回读的字节，才能作为设备级回退证据。报告明确保留 `deployment_eligible=false` 与 `flash_authorized=false`。

## 关键范围

候选布局是：

| 区域 | 起点 | 结束（不含） | 容量 |
| --- | ---: | ---: | ---: |
| `assets` | `0x5EC000` | `0xDEC000` | 8 MiB |
| `model` | `0xDEC000` | `0xEB4000` | 800 KiB |

因此候选写入可能影响 `0x5EC000..0xEB4000`。旧布局原 model 之后必须备份的尾部是 `0xC90000..0xEB4000`，共 `2,244,608 B`。只备份到 `0xDEC000` 会漏掉后移 model 的写入范围，生成器和负向测试都会拒绝这个范围。

现场备份还必须包括 bootloader、当前分区表、NVS、otadata、PHY、当前 App、当前 assets 和当前 model。九段要求可以由一份完整 16 MiB 读回覆盖，但必须在 manifest 中按段核对长度和 SHA-256。

## 受控写入计划

离线清单只允许候选布局中的三个目标：

1. 写入新 `model`，回读并核对；
2. 写入 8 MiB `assets`，回读并核对；
3. 最后提交候选分区表，回读并核对。

bootloader、App、otadata、NVS、PHY 和旧布局参考镜像不在候选写入序列中。分区表最后提交是风险控制顺序，不是断电原子更新；任何中断都必须回到 USB 下载模式，用现场回读和离线参考恢复，不能只刷 App。

## 离线验收

- 17 项 E1.35b 专项测试覆盖正确/错误分区表、模型摘要、成员摘要、0xFF 填充、缺失旧 model、缺失现场尾部、旧布局 flash map、路径穿越、额外字段、假部署授权、保护区和不可替换镜像。
- `scripts/build-expression-e1-35b-migration.py --check` 只验证已有 staged image、manifest、report、HTML 和 README，不会重写它们；默认生成也只写项目 checkout 下的 `tmp/e135b-migration-20261029` 和小型公开审阅产物。
- 页面显示旧/候选分区、完整影响范围、九段备份表、逐成员摘要和自动拒绝条件；不展示“已备份设备”的假结论。

本轮离线总检验运行 289 项：287 项通过，2 项因缺少真实 F1 导出或本机官方源码能力报告而跳过；E1.35b 的 17 项专项测试全部通过。页面校验覆盖 19 个工作台、22 段 JavaScript 和 18 份 JSON，发布检查覆盖 1,469 个文件。独立 Edge 桌面/窄屏测试通过，无横向溢出、脚本错误或网络请求，两个尺寸的截图均已人工审阅。`git diff --check` 通过。这些结果只证明离线包、元数据和审阅页符合检查，不代替设备迁移、运行内存或实机交互验收。

机器产物：

- 本地离线清单：`tmp/e135b-migration-20261029/manifest.json`（不进入公开仓库）
- [report.json](../artifacts/expression-storage-migration-e1-35b-20261029/report.json)
- [审阅页](../artifacts/expression-storage-migration-e1-35b-20261029/index.html)
- [Schema](../protocol/expression-storage-migration-e1-35b.schema.json)

下一阶段是 E1.35c，分三道闸门：先只读确认设备身份与九段原始备份，核对旧分区、当前 App、E1.18、model 和尾部回退；再单独授权迁移与逐段回读；最后采集实际 mmap/模型/堆/PSRAM 证据并由用户验收官方交互。如果设备 model 或填充与构建参考不同，应从设备原始镜像另生成迁移清单并复审，不能直接覆盖。新动画筛选、对称/非对称布局和小鱼资源归档仍是扩容稳定之后的 E1.35d/e。
