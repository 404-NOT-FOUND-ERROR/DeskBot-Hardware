# E1.43c-1：迁移与恢复控制器

更新：2026-11-03。电脑端实现和故障测试完成。设备沿用已验收的 E1.42 App、E1.40 bootloader、E1.18 资源和旧表；E1.43b 实测稍后进行。

## 本轮结果

- 46 项控制器、文件模拟和入口测试通过；53 项布局、资源及历史迁移回归通过。
- 两次独立进程演练分别中断资源和分区表写入。删除候选文件后，恢复结果与原 32 MiB 模拟镜像逐字节一致；再次恢复写入次数为零。
- 原上半区使用非 FF 数据，恢复保留原始内容。备份、计划、状态日志均绑定摘要。
- 模拟数据已压缩并逐成员校验，原始模拟目录清理记录另存；E1.43b 检查点和现场备份继续保留。

专项证据见[测试与恢复报告](../artifacts/expression-upper-migration-executor-e1-43c1-20261103/report.json)。全套 673 项：668 通过、2 跳过、3 项历史 E1.26 源码/构建检查未通过；明细和日志单列。

## 工作方式

| 环节 | 执行内容 |
| --- | --- |
| 准备 | 固定身份、同轮完整镜像、候选摘要与两个写入范围，保存原表及上半区原字节 |
| 迁移 | 先写 `0x1000000..0x1800000`，完整回读后提交 `0x8000..0x9000` 分区表扇区 |
| 状态记录 | 每次写入前保存意图；日志原子替换，进程退出后操作锁自动释放 |
| 恢复 | 先恢复并校验原表，再恢复上半区；沿用持久计划和原始字节 |
| 完成 | 核对保护区和 NVS 变化，保留待人工交互验收状态 |

控制器位于 `protocol/storage_upper_migration_transaction.py`，文件模拟器位于 `protocol/storage_upper_migration_simulator.py`。当前入口仅提供电脑端模拟；模拟启动检查的是表摘要。ESP32 启动、资源挂载和屏幕交互在实机阶段验收。

## 复核与演练

从实际 Git 工作树运行，解释器使用 Offline 预检确认的 Python 3.13。

```powershell
& '.\scripts\preflight-deskbot-toolchain.ps1' -Mode Offline
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' -m unittest discover -s tests -p test_storage_upper_migration_transaction.py -v
```

使用新的唯一标签，可演练一次中断和恢复：

```powershell
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' scripts/exercise-expression-e1-43c1-migration.py simulate --run-label e143c-sim-review-01 --interrupt-at table_write_intent
& 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe' scripts/exercise-expression-e1-43c1-migration.py recover-simulation --run-label e143c-sim-review-01
```

第一条预期退出码为 3，表示已注入模拟中断；恢复预期退出码为 0，报告中的 `full_image_equal` 为 `true`。文件保存在工作树 `tmp/<标签>/`；复测更换标签。

## 接下来的顺序

1. 离线审阅实机适配层：明确设备身份、分块回读、精确写入、恢复入口和启动证据；复用现有串口基础能力。
2. 用户方便时，完成 E1.43b 的三分钟连续映射、对话切菜单和原 App 恢复验收。
3. 核对同轮身份与 32 MiB 备份，按两个范围执行 E1.43c，验收原包挂载、显示、菜单、对话和摸头。
4. 扩容验收后导入一个新表情，再推进表达目录、角色绑定和客户端调度；底座维修后继续外壳识别支线。

开发方法：固定一个布局，先验证恢复，再连接实机；模拟、回读、启动和人工验收分别记录。
