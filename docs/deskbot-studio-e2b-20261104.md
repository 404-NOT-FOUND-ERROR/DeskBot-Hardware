# DeskBot Studio E2b · 角色草稿到资源包

E2b 把工作台中选定的角色与表情接线，生成可复现的 8 MiB 资源镜像和审计报告，并核对正式固件构建使用的 N32R16 分区布局。

## 使用

本机工作台：[打开](http://127.0.0.1:8766/)。从仓库根目录启动：

```powershell
python scripts/serve-deskbot-studio-e2b.py
```

在“发布”页生成当前角色的资源包，可下载完整资源镜像和报告。离线页面能查看已保存报告，但不能下载本机生成的二进制。服务只监听本机地址，提供健康检查、打包与包文件下载，不提供设备写入接口。

## 生成规则

- 按 `deskbot.studio_draft/v1` 校验草稿；动画必须适配眼部布局，聆听状态保持独立。
- 以 E1.18 完整资源镜像为基线。默认映射生成的镜像与基线逐字节一致。
- 候选素材只在本机使用。E1.45 单眼镜像害羞候选包为 **5,981,260 B**；候选媒体不进入公开工作台。
- 报告记录角色输入、目录、设备目标、成员差异、实际大小和 SHA-256。原资源成员逐字节保留，只更新需要变更的索引。
- 包生成器按内容摘要保存到 `tmp/deskbot-studio-e2b-packages-20261104/`，重复生成复用相同产物。

## 固件布局检查

隔离构建使用 ESP32-S3 N32R16 的 `upper-assets-8m` 布局：32 MiB Flash、16 MiB PSRAM，资源区 `0x01000000–0x01800000`，建议发布上限 7 MiB。App 为 **4,481,776 B**，分区剩余 **1,662,224 B**。构建报告核对分区表、bootloader、语音模型、App 余量与诊断开关；本轮设备写入次数为 0。

## 验证

本轮 Python 18 项、Node 8 项、Edge 浏览器 15 项通过。浏览器覆盖公开页与本机页、默认包和候选包、下载摘要、报告导入、草稿修改后的报告失效、未接线时禁用发布，以及 320/390/1440 像素布局；没有页面错误或外部网络请求。

完整离线套件共 794 项：787 通过、2 跳过、1 失败、4 错误。5 项问题来自 E1.26/E1.43 历史审计绑定的源码快照与当前源码不一致，涉及 `Kconfig.projbuild` 和 `SOURCE_MANIFEST.sha256`。历史快照需要单独整理；本轮保留原检查及完整输出。结果与摘要见[验证汇总](../artifacts/deskbot-studio-e2b-20261104/verification.json)。

```powershell
python scripts/build-deskbot-studio-e2b.py --check
python scripts/build-deskbot-studio-e2b.py --local --check
python -m unittest discover -s tests -p test_deskbot_studio_e2b.py
node --test tests/deskbot-studio-e2b-core.cjs
node tests/deskbot-studio-e2b-browser.cjs
```

固件构建和核对使用预检返回的 IDF Python。系统 Python 负责上面的离线测试，构建解释器负责 YAML、分区工具及编译环境。

```powershell
$DeskBotBuildEnv = .\scripts\preflight-deskbot-toolchain.ps1 -Mode Build | ConvertFrom-Json
$DeskBotIdfPython = $DeskBotBuildEnv.idf_python.path
& $DeskBotIdfPython scripts/build-deskbot-studio-e2b-firmware.py --check
```

新建构建时去掉 `--check`；中断后用 `--resume`。产物摘要见[构建报告](../artifacts/deskbot-studio-e2b-20261104/firmware-build-report.json)。

## 下一步

E2c 已在 [工作台与协议说明](deskbot-studio-e2c-20261104.md)中完成：电脑模拟器覆盖连接、重连、重复请求、忙碌、超时和动作事实。下一阶段先审计本体现有通信入口，再接只读状态与表现调度。
