# E3f-9a 独立审查

result: approved
reviewer: layout_manifest_review
date: 2026-11-12

审查覆盖清单加载、播放器与真实目录共用布局、镜像预览、资源打包、冻结构建和离线安装准备。当前源码与构建、测试证据一致，未发现待修正项。

## 已核对

- 清单采用整包校验后登记，检查格式、资源摘要、全部图像帧尺寸、重复项和数量上限；无效条目释放登记并采用既有布局。
- 登记表位于 PSRAM；播放和逐帧回调只查表。保留首帧重排、播放失败恢复、系统聆听布局及原菜单显示路径。
- 已修正两处预览差异：宽素材重叠区按 GFX 的逐列顺序绘制；125px 单眼关闭镜像后按整数坐标居中。主工作台与角色预览均已同步。
- 独立运行实际 GFX 4/8/24 位渲染测试通过，共 42 种宽度、透明模式与像素格式组合；本次再次运行 15 项 Node 像素测试全部通过。
- 留存自动测试：95 项 Python/native、44 项 Node、42 项浏览器检查全部通过。已核对 16 个审计源码、5 组证据及 20 个 on/off 构建产物的摘要。
- 两个资源候选均为 8 MiB，含 19 个布局条目；独立解析确认全部原 E1.45 成员逐字节保留。包内容为 5,986,416 B，软预算余量 1,353,616 B。

## 构建结果

| 项目 | 结果 |
| --- | --- |
| 默认关闭版 | 4,521,872 B；执行代码与数据和 E3f-7 相同，64 B 差异均在 ELF 摘要及镜像校验字段 |
| 开启版 | 4,524,592 B；App 余量 1,619,408 B |
| 内部静态数据 | 65,168 → 65,184 B，增加 16 B |
| 编译器函数栈记录 | 清单 Load 224 B；相关函数最大 1,392 B |
| 配置差异 | 仅 DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE |
| 保护产物 | bootloader、分区表、模型、初始 OTA 数据及刷写参数均匹配基线 |
| 开启版 App SHA-256 | 51b458b70d4f3cf72ebdc4583b760ac789475527b974b7a8807507c6273afaef |

离线 App 计划检查了既有安装记录、基线摘要与重建恢复快照。正式安装使用当轮设备备份生成恢复材料，资源安装与恢复单独执行。

## 后续验证

固件构建和离线审查已完成。本审查未打开串口或写入设备。接下来实测默认布局、单眼居中和镜像调整，再测试连续表情切换、菜单、对话、摸头及睡眠唤醒；同时观察设备端加载耗时与运行内存。主机分配测试和编译器单函数栈记录分别用于离线边界检查，不代表设备运行时峰值。

## 审查源码 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| `firmware/body/main/Kconfig.projbuild` | `deb0ad4f238759a10cfe4e3bbb97bb21b00ffbf1df4413ea50ef07ed26f756ed` |
| `firmware/body/main/boards/esp_vocat/studio_asset_layout.cc` | `de7040e72e56ad9d9fe5006d8bb33697356941d389f1a838ab8b33ced48bf796` |
| `firmware/body/main/boards/esp_vocat/studio_asset_layout_manifest.h` | `2970cb3e737b4e3fdbacd6efa740f3e96f12ffc1cfed40d5069f5b35ef1c5d73` |
| `firmware/body/main/boards/esp_vocat/studio_asset_layout_manifest.cc` | `96383c5a016668ef32ecbd0b1b7897222862fd2c12e2080eea6d2ac3e57680d1` |
| `scripts/build-studio-layout-manifest-e3f9a.py` | `4b37a1a881ebd2325838a0586fa3833fc7dbc723cfb702796c59374177a624fc` |
| `scripts/prepare-studio-layout-manifest-e3f9a.py` | `3d34da1a9deb9d300110054d24b8067390d34c81df9d67281739ba7de48a9366` |
| `scripts/prepare-studio-layout-release-e3f9a.py` | `df5eb47f93036384be53b360374edfe6ee701a163f7087b26a9233413e2a69b6` |
| `scripts/audit-studio-layout-manifest-e3f9a.py` | `705646ddf2b91901cbf800d3145af4d2fbc92ea68d9ab3f4d4743cedb9da36a7` |
| `scripts/deskbot-mirror-render.js` | `aa5de71bf94f542875136ebd545308a9a01ac76e6c87a54781c525f1086c0916` |
| `scripts/deskbot-runtime-roles-e3f8.js` | `8619c544d57f7fe2ccde67fbb061a40e5ca58243a2e0f91c1ed5de69d227510f` |
| `scripts/build-deskbot-studio-e3f8.py` | `5ad51599bc0f446bbb712a6a36f02256bb9b352c5d1cb3a9b51f1b2baf2d4009` |
| `protocol/studio_mirror_options.py` | `e16bca4023a83398e6b4cae025694715fd87cf26612eb810766409b5ff8c4f89` |
| `protocol/studio_resource_package.py` | `65ff4aa265b68bb5d1e192970e02f8df2f086ea084f71881781d1d239c7fdb43` |
| `docs/asset-layout-manifest-e3f9a.md` | `8a944fb59524f78c196fd7e2c5dd3268e9dbb5e711e1c15bd7073d579af0b753` |
| `tests/cpp/studio_catalog_test.cc` | `7f083d6c399c846e7fed1f64f3ecb1ead28cfffe819f8df89c22662d091bc1fb` |
| `tests/cpp/studio_mirror_pixels_test.cc` | `1e810314350efe0ef088e980a78b2e2a58eb9b80dcedf0356bc7247360cf083a` |
| `tests/cpp/studio_reflow_test.cc` | `adb31999dccf9cf149a21cfd9b233dfae735635aeac1fb7b25bf0e1315a14916` |
| `tests/studio-mirror-e3f8-browser.cjs` | `d7fcf78186d0083bcc3f31e269eeffb58831bbd70f8b217ce794f2f7c6f2b30e` |
| `tests/studio-mirror-render.cjs` | `4847b7cf30f3c2b7f921cc23adb9039e275c3c3e2dfaa0d7c9d96d4b5ec4d164` |
| `tests/test_studio_layout_manifest_e3f9a.py` | `7029ca75ca85ee107599f66bca180d720e22e7314a71d018f9107daeeeb64198` |
| `tests/test_studio_mirror_pixels_native.py` | `89890425d485bdcdf7c9c5ab2900d32363e054acf4c4b751b36197ebdc7dde6e` |

## 最终文档复核

result: approved。已复核设计说明、当前主线、开发计划、镜像说明与行动指南的本轮更新：源码实现、离线测试、构建数字、设备现用版本和后续顺序一致；镜像说明的浏览器检查数已更新为 42。86 处本地链接有效。行动指南已加入 GFX 整数居中、逐列镜像覆盖及中断构建接续步骤。复核时 16 个审计源码摘要与离线安装准备的报告摘要仍匹配。

| 文档 | SHA-256 |
| --- | --- |
| `docs/asset-layout-manifest-e3f9a.md` | `8a944fb59524f78c196fd7e2c5dd3268e9dbb5e711e1c15bd7073d579af0b753` |
| `docs/project-mainline.md` | `a9b5f1437293cfce5f00a854d09480717f1267ac563b95d20a9a7da340b6966b` |
| `docs/development-plan-20261104.md` | `a29065ee57a8f4c8201b4c2d8c4e71e12ffb3ae0fdb369acb3d199c52fa71cd5` |
| `docs/workbench-mirror-node-20261111.md` | `f992270971665f2c2fcb030ed3c49c3fb2361a329e550dc63c75a442a17f18da` |
| `docs/development-pipeline-20261102.md` | `e37fe30303d4bdbb0185752ad985a67b8fe3706bb796a950932769b86b1009e6` |
