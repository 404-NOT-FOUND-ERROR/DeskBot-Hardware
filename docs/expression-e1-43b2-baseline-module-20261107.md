# E1.43b-2 当前基线模块

日期：2026-11-07
阶段：E3c-3a 历史基线模块，代码、测试与复核完成。当前设备为已验收的 E3c-3b；下一轮按[适配设计](expression-e1-43b2-current-baseline-design-20261107.md)核对新基线。

## 模块范围

验证试验当时的 E3c-3a App 与 E1.43c-3 迁移后的静态区域。只读本地构建证据和输入快照。

## 接口

- `load_e3c3a_build_reference(root=ROOT)`：核对固定 E3c-3a 双配置报告、查询开启 App、嵌入的 ELF 摘要及受保护构建产物，返回本体 App 与布局基准。
- `validate_e3c3a_snapshot(snapshot, identity_sha256)`：从封存记录加载迁移与构建基准，再校验 32 MiB 快照、设备身份、当前 App 和迁移静态区域；跳过历史 E1.42 App 内容比较，并单独返回本轮 NVS 摘要。

## 测试方案

- 单元测试使用合成 32 MiB 快照，覆盖 E1.42 App 区域排除、E3c-3a App 摘要、设备身份、静态区、NVS 摘要、错误几何和截断输入。
- 证据加载测试核对归档双配置报告、镜像文件摘要、分区表和嵌入 ELF 摘要，并覆盖报告摘要错误。
- 所有测试均为离线测试，不打开 COM4、COM8 或其他串口。

测试命令：

```powershell
$DeskBotOffline = & .\scripts\preflight-deskbot-toolchain.ps1 -Mode Offline | ConvertFrom-Json
& $DeskBotOffline.offline_python.path -m unittest discover -s tests -p 'test_storage_continuous_mmap_e3c3a_baseline.py' -v
```

## 验证结果

- Offline Python 3.13.0、jsonschema 4.23.0。
- 专项测试 14 项全部通过，包含归档构建报告、镜像、分区表与嵌入 ELF 摘要的读取核验。
- 未打开串口、未访问设备、未构建或写入固件。

复核：独立 AI 审核通过，确认公开接口只能加载封存基准，测试覆盖与接口一致。
