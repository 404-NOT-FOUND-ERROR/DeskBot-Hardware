# E1.26 App 部署预检清单 / App Deployment Preflight

日期：2026-10-27。状态：**离线清单已生成，未连接串口，未刷写**。

本页只锁定下一次实机操作所需的本体 App 文件、摘要和边界。候选文件是启用 E1.26 被动表情调用观察器的完整构建产物；它只写 S3 本体 App 分区 `0x10000`。C61、资源分区、语音模型、分区表和 NVS 都不在写入范围内。

机器可读清单：[deployment-manifest.json](../artifacts/expression-e1-26-deployment-preflight-20261027/deployment-manifest.json)。构建审计：[E1.26 观察器审计](expression-runtime-observer-e1-26-20261027.md)。

## 文件与摘要

| 用途 | 本地文件 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| 候选：E1.26 observer enabled | `git_checkout_root/tmp/e1-26-full-body-build/o-on/xiaozhi.bin` | 4,477,280 | `f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2` |
| 回退：已验收 O2f2 App | `outer_workspace_root/backups/o2f2-accepted-20261018/body-app-0x10000.bin` | 4,472,496 | `4be89c2533909d06e9a8ef43a2aaa67ce9ec1e5a70a762f2827bf4388ac880a5` |

路径基准已经写入机器可读清单的 `path_base` 字段。候选产物位于 Git checkout 的本地构建目录；回退镜像位于外层工作目录的本地备份目录，两者都不是公开仓库文件，也不应从公开仓库路径猜测。`path_is_relative=true` 表示清单不包含机器专属绝对路径。

回退文件摘要已与 [`docs/baseline-status.md`](baseline-status.md) 中的 O2f2 记录一致。候选与回退不是同一个二进制；因此部署前仍要保留回退文件，并在写入前重新计算候选摘要。

## 写入边界

- 写入地址：`0x10000`。
- App 分区：`ota_0`（`app/ota_0`），来自 `firmware/body/partitions/v2/16m.csv`；分区表摘要为 `f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c`。
- App 分区容量：`0x5DC000`（6,144,000 字节），结束地址：`0x5EC000`。
- 候选文件实际写入范围：`0x10000..0x455160`；回退文件实际写入范围：`0x10000..0x453EB0`。
- 当前清单不包含整片刷写命令。实机步骤必须使用单一 App 文件和上述地址，不能使用完整工程的 `flash_args`。

## 写前闸门

用户另行确认实机操作后，先在已确认的本体端口上只读识别 ESP32-S3 芯片/MAC，并读取当前 App 摘要。再核对分区表、已验收 E1.18 表情资源和语音模型的边界；若当前 App 与归档回退不同，要把现场读取的 App 另存为第三份回退。写入前最后一次核对候选文件长度与 SHA-256，避免把观察器关闭版或其他构建误刷。

## 写后验收

只读回读本次 App 的完整实际写入范围并比较摘要，保留启动日志。确认没有擦除或写入 NVS、资源分区、模型、分区表和 C61。随后按已验收的回归表操作：屏幕与圆屏菜单、点击对话、对话中及结束后切菜单、摸头、睡眠/唤醒、声源跟随和表情。观察器快照只证明 `SetEmotion` 调用进入，不证明 LCD 已播放或电机动作完成；实机通过后才可把状态改为 `accepted`。

当前清单仍保持 `deployment_eligible=false`、`flash_authorized=false`。本轮没有访问 COM4/COM8，也没有写入设备。
