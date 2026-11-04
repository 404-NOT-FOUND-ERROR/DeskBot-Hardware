# E1.15 单语义提案 / Single-Semantic Proposal

日期 / Date: 2026-10-24 · 结果 / Result: `offline_validated`

[打开工作台](../artifacts/expression-e1-15-surprised-shocked-20261024/index.html) · [机器可读结果](../artifacts/expression-e1-15-surprised-shocked-20261024/decision.json) · [当前基线](baseline-status.md)

## 提案

本轮只审阅 `surprised: Happy.eaf → shocked.eaf`。`shocked.eaf` 已是官方资源，不新增 EAF；它和 `Happy.eaf` 都使用现有 `eye_anim` 圆屏布局、20 FPS 与循环设置。动画通过归档的 E1.10 主机解码图集并排播放，右侧可切换提案/逻辑回退。图像比较不替代实际圆屏 LCD 播放验收。

`surprised` 与 `shocked` 在官方动作映射中都是 `look_around`；动作映射不变，也不意味着动作已完成或由 EAF 触发。提案后 `Happy.eaf` 仍被 9 个语义引用，因此不删除且回收 0 B。这个提案会让 `surprised` 和 `shocked` 共享一套视觉，不会增加动画多样性；若目标是增加独立表情，需另行制作和评审素材。

## 容量口径

| 口径 | 完整包 | 分区余量 | 说明 |
| --- | ---: | ---: | --- |
| 已验收 E1.13 | 5,538,349 B | 605,651 B | 带包摘要与完整分区回读摘要的基线 |
| E1.10 序列化变化投影 | 5,537,968 B | 606,032 B | 对 E1.13 已验收尺寸应用旧离线审计得出的 `index.json -381 B`；未重新打包，无候选 SHA-256 |
| 仅文件名字节变化的敏感性对照 | 5,538,351 B | 605,649 B | 只算 `Happy.eaf` → `shocked.eaf` 的 UTF-8 文件名差 +2 B；不是序列化包大小 |

分区为 6,144,000 B。E1.13 相比上游仅无损重编码 `insert.eaf` 和 `eat.eaf`，因此 E1.10 的映射重序列化差值可以作为有条件的包大小投影；但本公开输入没有 E1.13 完整包，故没有生成当前版本包、做当前逐成员回读或计算候选包摘要。旧 `-381 B` 来自 E1.10 序列化重排，不代表复用 `shocked.eaf` 释放 EAF 空间。刷写前必须对当前 E1.13 包实际重打包、逐成员比对并验证回退。

## 差异与回退

- 逻辑快照仅改变 `surprised` 的资源引用；其余语义、20 FPS、循环、`eye_anim` 布局与动作映射均保持不变。
- 计划包仍是 29 个成员：新增/删除均为零；预期只重序列化 `index.json`，EAF payload 不变。这是提案，不是当前 E1.13 二进制包逐成员验证结果。
- 逻辑回退为 `surprised → Happy.eaf`，测试确认恢复后的逻辑快照与原快照完全相同。将来若部署，必须保留 E1.13 原始 index/package 的原始字节身份，不能以逻辑等价的重新序列化代替字节级回退。
- 发布与替换未授权；没有构建固件、创建新包、访问串口/硬件、修改 C61、NVS 或设备 profile。

## 后续闸门

1. 用户先审阅两套官方画面，决定是否接受语义合并；如果要的是新表情，本提案不满足目标。
2. 取得并固定原 E1.13 完整包后，仅重打包索引；比较所有成员摘要、完整大小、分区边界与逐字节回退。
3. 检查当前 EAF 播放器、圆屏遮罩、循环、帧率与资源内存边界；不得从静态工作台推断设备兼容。
4. 只有单独授权后才能仅部署 assets 分区，再做屏幕、菜单/对话、摸头、睡眠/唤醒回归；底座动作另待维修后验证。

## English Summary

E1.15 is an offline-only proposal to map `surprised` from `Happy.eaf` to the already shipped `shocked.eaf`. It changes no EAF payload, action mapping, timing, layout, or other semantic. `Happy.eaf` remains referenced by nine semantics, so no animation bytes are reclaimed. The page previews both official animations and a logical rollback, but does not establish LCD or device playback compatibility.

The accepted E1.13 package is 5,538,349 B with 605,651 B free. Applying the historical E1.10 reserialization delta yields a **projection**, not a rebuilt package: 5,537,968 B / 606,032 B free. The public checkout lacks the complete E1.13 package, so no candidate package hash or current member-by-member comparison is claimed. Deployment remains unauthorized; retain the original E1.13 serialized package for byte-exact rollback.

## Verification

```powershell
python scripts/build-expression-e1-15-surprised-shocked.py --check
python -m unittest tests/test-expression-e1-15-surprised-shocked.py
python scripts/check-publication.py
python scripts/check-workbenches.py
python scripts/run-offline-checks.py
git diff --check
```

The controller smoke uses a DOM/canvas double. It is not real browser rendering, physical LCD testing, firmware build, or hardware acceptance.
