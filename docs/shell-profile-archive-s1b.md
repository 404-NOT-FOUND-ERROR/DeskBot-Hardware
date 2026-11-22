# S1b：电脑端外壳档案保存

目标：已登记的磁场档案可保存、重新加载和备份，为外壳绑定角色提供稳定编号。本轮使用历史日志完成电脑端实现，E3f-9d.1 继续用于双角色验收。

## 方案

数据流：S1a 日志回放 → 登记参数与档案 → 带摘要的 JSON 文件 → 新识别器加载 → 独立记录重新识别。保存的是登记档案；采样窗口、最近身份和确认次数从空状态开始。M2 算法与阈值沿用现有实现。

档案包含版本、识别引擎、完整参数、传感器编号、安装坐标基准、XYZ 统计、登记时间、训练记录摘要和来源。传感器编号与安装基准由采集者填写；改变传感器或安装方向时另建基准。历史示例标为离线回放，实测进度继续由 S1a 管理。

| 接口 | 作用 |
| --- | --- |
| `MagneticRecognizer.from_profiles(profiles, config=...)` | 校验档案并创建空采样状态的识别器 |
| `build_archive(report, context)` | 从本次 S1a 回放结果生成档案，保留来源 |
| `read_archive(path, expected_context)` | 核对版本、摘要和传感器/安装基准，返回档案与文件摘要 |
| `restore_archive(archive, expected_context)` | 创建可接收新样本的 M2 识别器 |
| `save_archive(path, archive, expected_sha256=None)` | 新建文件；更新时核对读取版本并原子替换 |

同一档案最多沿用配置规定的 16 个默认档案，文件上限 256 KiB。写入采用同目录临时文件、刷新后替换；同一路径只允许一个写者。更新须提供上次文件摘要，写入中断或版本冲突保留原文件。CLI 的输入、输出均位于项目内，生成目录放在 `tmp/` 或 `artifacts/`。

内容摘要使用 UTF-8、键排序和紧凑 JSON，排除摘要字段本身；版本核对使用实际文件字节摘要。`expected_sha256=None` 表示只新建；路径锁覆盖旧文件核对到替换全过程。加载时校验唯一编号、完整数值和稳定窗口条件。S1a 增加 `saved_profiles` 回放参数，直接使用保存的登记数据评估独立记录。

依赖：Python 标准库、现有 M2 识别器与 S1a 回放工具。电脑档案与设备 RAM 档案分别管理；设备持久化和实时绑定接续实测结果。

## 验证与交付

单元测试覆盖：参数与传感器匹配、数量和数值边界、旧状态清空、损坏/重复字段、原子写入、写失败、版本冲突和多写者。集成使用真实历史日志：保存后由新进程加载，复播装回和取下记录，与原结果逐项比较。恢复档案不携带已确认身份，新采样仍经过稳定窗口和连续确认。

交付：代码、测试、JSON 档案示例、简洁结果预览和独立 AI 复核。接续顺序：双角色验收 → 固定外壳采集与评估 → 使用正式档案 → 外壳绑定角色 → 人格和声音。

## 本轮结果

电脑端保存与加载已完成。使用历史上移、下移两份登记档案，独立新进程加载后，装回和取下回放与保存前一致；加载初始为 `missing / no_sample`，新采样仍完成稳定窗口与连续确认。示例文件标记为 `offline_replay`，真实外壳采样接续双角色验收。

27 项专项、28 项 S1a、31 项识别与遥测测试通过。专项包含实际双进程竞争、写入/刷新/替换失败、版本冲突、来源和安装基准检查。独立复核补齐逐项训练来源核对与极大数值边界。示例与[结果预览](../artifacts/shell-profile-archive-s1b-20261112/restore/index.html)位于 `artifacts/shell-profile-archive-s1b-20261112/`。

复现命令使用 Offline Python：

```text
scripts/prepare-shell-profile-archive.py --suite protocol/shell-repeatability-s1a-history.example.json --sensor-id history-bmm150-20261107 --frame-id repair-fixed-base-20261107 --sensor-kind 1 --output artifacts/<新目录>
```

另开进程，在相同参数后加 `--restore artifacts/<先前目录>/profiles.json`，输出到另一新目录，即可核对加载后的独立回放。`sensor-id` 表示这颗传感器，`frame-id` 表示这次固定的安装坐标基准。
