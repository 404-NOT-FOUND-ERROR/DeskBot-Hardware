# E1.45：已选表情的资源包准备

已按本轮确认选定单眼镜像版，并完成完整资源镜像、恢复参考、包差异审计和前后预览。设备继续使用 E1.18 资源，三分钟实机测试待安排。

## 本轮成果

| 项目 | 结果 |
| --- | --- |
| 表情接线 | `embarrassed：Happy.eaf → shy_candidate.eaf` |
| 播放 | 160 × 200 单眼镜像，188 个图像帧，25 FPS，整段循环 |
| 包大小 | 5,981,260 B；与 E1.44 投影完全一致 |
| 完整镜像 | 8,388,608 B；包后填充 `0xFF` |
| 容量 | 分区余量 2,407,348 B；距 7 MiB 发布软上限 1,358,772 B |
| 恢复参考 | 已迁移 E1.18 的完整 8 MiB 资源，摘要一致 |
| 设备访问 | 本轮全程离线 |

完整镜像在本机 `tmp/expression-e1-45-staging-20261104/`。该目录只保留候选、恢复参考、清单和报告，不重复保存裸包。候选完整镜像 SHA-256 为 `e5ac78a187866cff05a16e5fd223c934a45d6cb4bf13dfcd1766cc93db805748`。

## 已核对的差异

重新打包并解析后，新增一个 EAF，索引只修改 `embarrassed` 的文件和 FPS。其他 8 个 Happy 引用、`surprised → shocked.eaf`、布局、字体、图标、模型引用及 `CAT_NUZZLE` 动作保持原样。

从完整镜像提取的候选与 E1.44 逐字节一致，播放器源码和 DLL 摘要也一致，因此沿用该候选的 376 次 C 渲染对照与 4 个默认时序场景证据，本轮没有重复跑 C 审计。

已用保存的迁移回读建立 32 MiB 内存副本，检查完整资源替换、写入一半中断两个场景。两种场景都只改变活动资源区，恢复后的整片副本与原内容逐字节一致。这是离线字节范围与恢复检查；现场写入前另外保存当轮备份。

新增 11 项测试覆盖单语义差异、共享资源、布局、容量、填充、恢复范围与报告错误。预览页展示当前 Happy 与已选镜像版，提供播放、逐帧与末帧保持。

## 查看与复现

- [公开准备页](../artifacts/expression-e1-45-staging-20261104/index.html) · [选择记录](../artifacts/expression-e1-45-staging-20261104/selection.json) · [机器报告](../artifacts/expression-e1-45-staging-20261104/report.json) · [验收记录](../artifacts/expression-e1-45-staging-20261104/verification.json)。
- 本机动态预览：`tmp/expression-e1-45-staging-preview-20261104/index.html`。候选媒体与二进制继续留在本机忽略目录。

```powershell
python scripts/prepare-expression-e1-45-staging.py --check
python -m unittest discover -s tests -p test_expression_resource_staging.py -v
python scripts/build-expression-e1-45-staging.py --check
python scripts/build-expression-e1-45-staging.py --local --check
node tests/expression-e1-45-staging-browser.cjs
```

完整重建使用不带 `--check` 的准备和页面生成入口，需要留存的 E1.44 候选、官方参考与迁移回读。原始完整脸继续保留在母库。

## 下一步

1. 方便时完成 E1.43b-2 三分钟连续映射和交互测试。
2. 核对本体身份、当前 App、分区表与资源，保存当轮 32 MiB 备份和资源恢复材料。
3. 只部署 `0x01000000..0x01800000` 活动资源区，完整回读后启动。
4. 验收新表情、摸头、对话中及结束后切菜单、睡眠唤醒。
5. 通过后进入表达目录和客户端受控请求；底座修复后继续外壳登记支线。
