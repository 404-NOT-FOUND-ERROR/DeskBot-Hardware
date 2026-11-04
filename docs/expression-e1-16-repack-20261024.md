# E1.16 真实资源包重打包审计 / Actual Package Repack Audit

日期 / Date: 2026-10-24 · 结果 / Result: `offline_repack_verified`

[打开审计工作台](../artifacts/expression-e1-16-repack-20261024/index.html) · [机器可读结果](../artifacts/expression-e1-16-repack-20261024/report.json) · [E1.15 提案](../artifacts/expression-e1-15-surprised-shocked-20261024/index.html) · [当前基线](baseline-status.md)

## 结论

E1.13 的完整资源包已经从本地归档中重新取得并逐字节核对：

- 包大小：`5,538,349 B`；SHA-256：`5e342db35da13531836339a98c25d6e74ac6fdff446ad4e515fcf39f28b7e81f`。
- 29 个成员、32 字节文件名槽位和官方旧版 MMAP 序列化可重现。
- 相对官方 `5,889,001 B` 包，仅 `insert.eaf`、`eat.eaf` 两个 EAF 是 E1.13 已验收的无损重编码。
- E1.13 完整分区回读归档为 `6,144,000 B`，SHA-256 为 `6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205`，并与包后 `0xFF` 填充逐字节一致。

在此真实基线上，E1.15 的单语义提案只把 `index.json` 中 `surprised` 的文件名从 `Happy.eaf` 改为已有的 `shocked.eaf`。本轮保持索引的 CRLF、缩进和其他所有字节，不重排 JSON：

- 候选包：`5,538,351 B`，SHA-256：`9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1`。
- 分区余量：`605,649 B`；仍在 `6,144,000 B` 边界内。
- 29 个成员中只有 `index.json` 改变；所有 EAF 和图标成员逐字节相同。
- 候选包回退到 E1.13 原始索引后，与原包和已归档完整分区均逐字节相同。

因此，E1.16 的“实际包”闸门已通过，但这不是部署授权，也不是设备验收。页面和候选二进制都不能替代用户对两套圆屏画面的采用决定、播放器运行时复核和实机回归。

## 差异边界

| 层级 | E1.13 基线 | E1.16 候选 | 结论 |
| --- | ---: | ---: | --- |
| 完整包 | 5,538,349 B | 5,538,351 B | 仅文件名 token 增加 2 B |
| 分区余量 | 605,651 B | 605,649 B | 通过 |
| 成员数 | 29 | 29 | 不新增、不删除 |
| EAF payload | 29 个成员中的全部 EAF | 完全相同 | 不改变动画帧、编码、FPS 或布局 |
| `index.json` | `surprised → Happy.eaf` | `surprised → shocked.eaf` | 唯一变化 |
| `look_around` 动作 | 保持 | 保持 | 不驱动电机、不改动作映射 |

`Happy.eaf` 仍被 9 个语义使用，不能因为 `surprised` 移出就删除或宣称回收动画空间。`shocked.eaf` 原本已在包内，因此本次没有增加新 EAF。

## 回退与证据边界

回退不是重新生成一个“逻辑等价”的索引，而是保留 E1.13 的原始索引字节并重新打包后做全包比较。审计结果同时验证：

1. 候选包的 28 个未改变成员字节和摘要不变；
2. 恢复原始 `index.json` 后，包 SHA-256 恢复为 `5e342db3…b7e81f`；
3. 包后补 `0xFF` 到 `6,144,000 B` 后，完整分区 SHA-256 恢复为 `6b1bce87…c205`。

本公开工作树只发布摘要、成员哈希和工作台。原始输入、候选包和回退包保留在本机项目私有 `tmp` 中，不写入 GitHub，不包含串口号、MAC、NVS 或凭据。

## 仍未通过的闸门

1. **播放器兼容性**：静态 EAF/索引检查通过；尚未把候选资源交给设备播放器运行。
2. **视觉采用**：请先在 E1.15 工作台确认 `Happy` 与 `shocked` 是否符合你要的语义；这只是复用官方画面，不会增加独立表情。
3. **部署授权**：若采用，必须单独授权只刷本体 assets 分区 `0x5EC000`、长度 `0x5DC000`，不刷 App、C61、NVS，不擦除 Flash，并完整回读。
4. **实机回归**：刷后按 O0/O1 方法测试圆屏、点击对话、对话中/后菜单、摸头、睡眠/唤醒；底座维修后再验收 `insert/eat` 和动作。

## 复现（本机私有输入）

```powershell
python scripts/audit-expression-e1-16-repack.py `
  --baseline "<private-project>\logs\expression-e1-12-integration-20261023-run1\expression_assets-e1-12-final.bin" `
  --official "<private-project>\tmp\remote-esp-vocat-official-build\b-events-v1\expression_assets.bin" `
  --readback "<private-project>\logs\expression-e1-13-20261023-run1\assets-after-flash-readback.bin"
```

该命令只写本机 `tmp/expression-e1-16-repack-20261024/` 和公开审计工作台；不打开串口，不构建固件，不执行 `esptool`。公开测试只校验已发布的摘要，不会因缺少私有原始包而伪称可重建。

## English summary

E1.16 reconstructed the accepted E1.13 package from a private local archive and verified the legacy MMAP serializer byte-for-byte. The candidate changes exactly one UTF-8 filename token in `index.json`: `surprised: Happy.eaf → shocked.eaf`. It adds 2 bytes, keeps all 29 members and every EAF payload byte-identical, fits the 6,144,000-byte partition, and rolls back byte-for-byte to the E1.13 package and padded partition. No firmware build, serial access, C61/NVS change, flash or deployment authorization occurred. Player compatibility, visual adoption and hardware regression remain separate gates.
