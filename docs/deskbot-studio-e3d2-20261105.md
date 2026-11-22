# E3d-2 · 设备实时资源目录

E3d-2 在 E1.18 已验收资源快照旁，增加当前设备会话的只读目录。它沿用 E3c 的 USB 所有者和会话，按页读取挂载的表情、布局及原动作映射。收齐全部页面并核对 SHA-256 后，工作台才显示完整目录。

[打开工作台](../artifacts/deskbot-studio-e3d2-20261105/index.html) · [离线报告](../artifacts/deskbot-studio-e3d2-20261105/offline-validation.json) · [完整回归](../artifacts/deskbot-studio-e3d2-20261105/full-offline-validation.json) · [浏览器报告](../artifacts/deskbot-studio-e3d2-20261105/browser-validation.json) · [固件双构建报告](../artifacts/deskbot-studio-e3d2-20261105/firmware-build-report.json)

## 工作台

“设备实时目录”与 E1.18 快照分开显示。打开设备状态查询后，点击“读取实时目录”；页面显示分页进度，收到完整清单并核对摘要后展示条目及资源包摘要。部分页面不会显示成完整目录。

读取期间，工作台暂停新的 neutral 请求。USB 会话仍每秒查询一次显示状态；显示查询和目录分页请求共用同一请求序号、nonce 和 400 ms 主机节流，不增加串口所有者。失败重读、服务出错、设备重启、断开和会话到期都会清空本次实时清单。

## 固件与主机

固件仅在资源所有者成功挂载 EAF 后，从当前 `index.json`、已加载资源和包分区生成有界快照。最多 128 条、每页 2 条，快照使用 PSRAM；USB 会话复制小页后格式化回复。失效发生在资源卸载或重载前，generation 检查避免发布旧快照。目录接口只读，也不会执行动作。

电脑端 `CatalogQueryClient` 复用显示查询客户端的会话边界。目录页绑定设备、启动、会话、请求和序号；混页、重放、超时或身份变化时丢弃整份目录。动作完成仍只按 `fact_only` 展示。

## 离线验证

| 检查 | 结果 |
| --- | --- |
| 固件默认关闭/启用隔离构建 | 通过；App 分别为 4,510,480 B / 4,517,968 B |
| App 分区余量 | 1,633,520 B / 1,626,032 B |
| 实际 E1.18 包目录与电脑端清单逐项比较 | 通过 |
| 生产 Builder、Adapter、USB Session 编译测试 | 13 项通过；使用 ESP-IDF 5.5.5 携带的 cJSON 1.7.19 与 Mbed TLS 3.6.6 |
| 主机分页、会话与重连专项 | 9 项通过 |
| 工作台生成与服务 | 3 项通过 |
| 历史报告与源码审阅 | 8 项通过 |
| 工作台浏览器 | 40 项通过；桌面 1440 px、手机 390/320 px、深色和减少动态效果 |
| 完整离线回归 | 1,294 项：1,292 通过、2 项条件跳过 |

构建保持同一 bootloader、分区表、模型与 NVS 设置。离线检查没有打开串口或写入设备。当前 COM4 运行 E3c-3a 查询开启版，菜单、摸头、睡眠唤醒及只读 `open`/`state` 已验证；E3d-2 尚未刷入。下一步核对显示查询与部署回退材料，再测目录读取和 neutral 交互。

2026-11-06 后续实机记录：E3c-3 已写入并启动，但 USB 输入不可用，查询未进入；随后恢复 E3c-2，用户确认静置、唤醒、左滑菜单并返回正常。实时目录联调待 USB 路径核对和菜单回归完成。

两项跳过分别需要真实 F1 导出和本机官方资源能力报告。历史报告保留原始字节；共享源码的旧、新摘要与原因记录在[源码审阅清单](../artifacts/deskbot-studio-e3d2-20261105/source-review.json)，当前代码另跑本轮检查。

## 启动

从仓库根目录运行：

```powershell
& .\scripts\start-deskbot-studio-e3d2.ps1
```

工作台位于 `http://127.0.0.1:8772/#connection`。启动服务本身不连接串口；页面中明确连接设备后才由唯一 USB 所有者打开连接。

离线复核使用 Offline 预检选定的 Python：

```powershell
python scripts/validate-deskbot-studio-e3d2.py --check
python scripts/validate-deskbot-studio-e3d2.py --full --check
python scripts/build-e3d2-source-review.py --check
```

## 继续顺序

1. 逐版定位并修复菜单问题，完成对话、菜单、摸头和睡眠唤醒回归，再验证支持 neutral 请求的版本。
2. 部署 E3d-2 App，读取同一 USB 会话内的目录，与 E1.18 的 36 项快照逐项比较；验证断开重连与重新读取。
3. 完成目录和交互验收后，逐个开放纯表情请求；底座修好后继续已有动作与外壳登记。
