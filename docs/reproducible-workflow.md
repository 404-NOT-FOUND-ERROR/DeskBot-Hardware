# 可复现增量开发 / Verification Workflow

当前工作台使用 [E3b 复现入口](deskbot-studio-e3b-20261104.md)：页面、设备身份、协议模拟和资源包分别核对；E3b 固件双配置构建与实机部署各有独立报告。Offline Python 3.13 跑离线测试；构建和串口使用固定 IDF Python 3.11，完整路径由环境预检提供。

本轮常见问题与固定做法：

| 情况 | 做法 |
| --- | --- |
| 离线 Python 没有 serial 模块 | 运行 start-deskbot-studio-e3b.ps1；它核对两个 Python，由独立串口进程读取 |
| 主机可运行的格式字符串在设备崩溃 | 构建前检查实际 libc 配置；nano 格式下，64 位整数先转十进制文本 |
| 新服务接入后打包页提示版本不匹配 | 保持健康接口 service=deskbot.studio.packager/v1，新增版本信息放在 edition 字段 |
| 串口打开后没有当前状态 | 等待下一次状态变化；定期内存日志可以确认接收链路，不据此填入待机状态 |
| 轮询使下拉菜单或模拟器闪回 | 只更新观察卡片，保留展开状态和输入焦点；模拟区与实况区分开 |
| PowerShell 嵌入 Python 引号报错 | 复用仓库脚本；多行代码使用单引号 here-string，经标准输入交给 Python |

## 一轮只跨一个风险边界

1. 固定上游 commit、源码/配置哈希、目标芯片、基线与预期行为；记录禁止修改的文件/分区。
2. 先做离线协议、错误输入、事件去重、过期/乱序、资源边界与内存上限测试。
3. 独立目录构建，检查 App 和资源容量、分区表、模型及所有未修改成员；保持原始备份。
4. 用户独立授权后才识别明确端口的芯片/设备。COM 号不是永久身份，S3 与 C61 分开。
5. 仅写获准范围，不全片擦除，不覆盖 NVS；App 与 assets 不能混为同一授权。
6. 全范围回读比较摘要和字节，核对未改 App/分区表；记录启动日志。
7. 用户实机验收显示、对话中/后切菜单、摸头、睡眠/唤醒、DOA/动作及本轮触发。
8. 失败停止叠加；保留证据，恢复同范围已知良好备份。只有用户回归通过才标 accepted。

Build success, deployment integrity and physical acceptance are separate claims. A 25-second clean boot does not prove UI stability. Git publication does not authorize a device write.

### 容量与模式的额外闸门（E1.38）

物理 Flash 接口、镜像头模式、编译选择、运行驱动模式分别固定证据。运行时 auto-detect 不代表编译时 cache32 已开启；只读全片备份也不代表 EAF 所需 mmap 已可用。容量生效来源由 bootloader 头与 `SIZE_OVERRIDE` 决定，不能仅看新 App 的容量字节。

回退必须来自现场完整擦除扇区，覆盖 payload 后的末扇区原字节；不使用 build bootloader 或 FF 填充冒充现场数据。历史快照须在写前刷新设备身份、实时摘要和受影响范围；新镜像长度改变即重新计算回退。容量启动保持旧布局和当前资源，上半区资源迁移另轮验证。具体证据见 [E1.38](expression-addressing-e1-38-20261030.md)。

### 显式 Octal 候选的复核方法（E1.39）

E1.39 在项目 `tmp/` 的隔离副本顺序构建 16/32 MiB 控制与候选；正式产品源码、旧分区表和配置输入保持原样。固定 OPI、STR、80 MHz、MXIC 驱动，关闭自动识别、DTR、Quad cache32、实验选项和容量 override；32 MiB 的 Octal cache32 由 IDF 条件派生。镜像头 `dout` 是 IDF 对 OPI 的编码表示，不能用它推断物理 Flash 为 DOUT。

审计依次重读实际依赖内容、完整 sdkconfig、App/bootloader 各自生成的 JSON 配置、校验和/摘要及全部镜像段，再比较 ELF section 和函数地址/长度。显式模式会改变条件编译代码，不能沿用 E1.37 的“只改容量头”结论；所有额外配置差异都须有明确源码依据。checkpoint 保存输入、原调用脚本、产物和日志摘要；缺少证据即失败。

```powershell
python scripts/build-expression-e1-39-octal.py --check
python scripts/audit-expression-e1-39-octal.py --check
```

这两个命令只复核本机保留证据，需要对应 ESP-IDF/pyelftools、私有构建和历史全片；不会重新编译或访问设备。`--review-completed` 只更新完成构建的审阅结果，保留原始脚本身份。公开发布检查和默认可移植测试仅核对脱敏元数据一致性，不把无法取得的私有字节验证说成通过。

构建的 `expression_assets.bin` 是原源码参考资源（5,889,001 B），不是已验收 E1.18 活动包（5,538,351 B）；构建 `flash_args` 不能直接执行。容量启动须按获准的成对镜像制作最小清单、刷新现场身份/备份/保护摘要，并按新 payload 长度保存完整擦除扇区。上半区 guard 放行只代表编译路径存在，尚需真实 mmap、模型、内存和官方交互验收。

## 公开发布

使用独立 clone、正常提交与 push，保留远程历史。只发布源码、必要构建输入、协议、脱敏样例、工具和可视化。排除完整 Flash、NVS、Wi-Fi/API 凭据、MAC 运行日志、工具链缓存与本机绝对路径。依赖锁中的本地组件改成仓库相对路径，并公开记录这一包装差异。

```powershell
python -m pip install -r tests/requirements-body-facts-validation.txt
python scripts/check-publication.py
python scripts/run-offline-checks.py
git diff --check
```

CI 的主机测试范围由 `scripts/run-offline-checks.py` 明确列出，不以“全量测试通过”概括未执行的 ESP-IDF、浏览器或设备测试。依赖本机私密证据的历史集成脚本保留研究价值，但不在干净 clone 上宣称可直接复现。
