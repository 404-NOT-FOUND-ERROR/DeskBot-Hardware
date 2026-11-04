# E1.36 N32R16 容量布局闸门

更新：2026-10-30。该阶段只做离线容量与分区布局审计，不访问串口、不刷写 Flash、不擦除 NVS，也不接触 COM8。

## 目标

把三个容易混淆的容量事实拆开：COM4 本体的物理 Flash 是 32 MiB，当前已验收镜像头和正式分区表仍按 16 MiB 配置，PSRAM 是 16 MiB 运行内存。完整现场回读 `tmp/e135c-live-20261029/body-full-32MiB.bin` 已由 32 个 1 MiB 分块和独立 `verify_flash --flash_size keep` 校验，整片 SHA-256 为 `21e68e726db1e6c4a9b50c05f2dc1505a049feda5d0f6dbc872a67c2bddb1f6e`；本轮只消费这份只读证据。

## 候选布局

| 候选 | 变化 | 用途 | 当前结论 |
| --- | --- | --- | --- |
| `current-16m` | 已验收旧表、旧资源和旧模型 | 精确回退 | 保持设备基线 |
| `capacity-only-32m` | 只将配置容量改为 32 MiB；分区行和地址逐字节不变 | 下一次独立构建 | E1.36 推荐；还没有构建、启动或刷写 |
| `upper-assets-8m` | 保留旧 `assets` 为 `assets_prev` 和旧 `model`，在 `0x1000000..0x1800000` 增加 8 MiB `assets` | 资源扩容候选 | 后续候选；需要新表、整区 mmap 和回退验证 |
| `inplace-assets-8m/12m/16m` | 原位扩大资源区并后移 model | 对照研究 | 不作为默认路线，会覆盖现场旧 model/尾部 |

当前 E1.18 活动包为 5,538,351 B。上半区 8 MiB 候选留出 2,850,257 B 物理余量，并保留 1 MiB 发布软上限；这是空间算术，不是运行时可用内存或资源配额承诺。旧资源、内嵌模型和独立 model 的职责分别保留，不能因为资源加载通常走内嵌模型就删除独立 model。

## 已完成检查

- 六份布局由 ESP-IDF 5.5.5 `gen_esp32part.py` 生成并反向解析，行、地址、大小和分区表哈希一致。
- `capacity-only-32m` 与 `current-16m` 的分区行和表二进制相同；它只是在后续构建中验证 32 MiB 配置边界。
- NVS、OTA data、PHY 和 App 保护分区没有变化。
- 8 MiB 上半区候选不覆盖旧资源或 model；原位候选被明确标为比较方案。
- 64 KiB mmap 页数、资源软上限、Flash 越界、重复/重叠分区和报告篡改都有负向测试。
- 报告固定 `deployment_eligible=false`、`flash_authorized=false`，并记录串口、写入、NVS、C61 均未访问。

机器报告和自包含页面见 [E1.36 布局报告](../artifacts/expression-storage-n32r16-e1-36-20261030/index.html)。生成/检查入口：

```powershell
python scripts/audit-expression-e1-36-n32r16-layout.py
python scripts/audit-expression-e1-36-n32r16-layout.py --check
python -m unittest tests/test_storage_n32r16.py -v
```

## 当前后续入口（更新于 2026-11-03）

容量启动和 A7 单页实测已经通过；E1.43b 连续 8 MiB 映射已完成构建，实机测试稍后进行。最新入口见[开发主线](development-pipeline-20261102.md)。

当前采用 `upper-assets-8m`：旧资源与独立 model 原位保留，先写上半区资源并完整回读，再最后提交分区表。E1.35b 的“移动 model”步骤属于原位对照方案。E1.43c 已完成[离线迁移预审](expression-upper-migration-e1-43c-20261103.md)；后续执行使用同轮新鲜备份和专用恢复记录。
