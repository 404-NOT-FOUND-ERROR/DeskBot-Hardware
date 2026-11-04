# E1.18 资源映射部署回执 / Assets Mapping Deployment Receipt

日期 / Date: 2026-10-24 · 结果 / Result: `deployed_integrity_verified`; body interaction accepted; exact animation choice pending

## 做了什么

在已验收的 O2f2/E1.13 本体上，只写入 assets 分区中的一份真实资源包。唯一语义变化是：

```text
surprised: Happy.eaf → shocked.eaf
```

`shocked.eaf` 原本已经存在，因此没有新增 EAF、没有删除 EAF，也没有改动播放器、动作、触摸、DOA、音频或底座固件。

| 项目 | 结果 |
| --- | --- |
| 写入范围 | 本体 assets：`0x5EC000..0xBC8000` |
| 写入长度 | `0x5DC000` = 6,144,000 B |
| 候选包 | 5,538,351 B |
| 候选包 SHA-256 | `9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1` |
| 完整分区 SHA-256 | `d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6` |
| 分区剩余空间 | 605,649 B |
| 回退分区 | E1.13 原分区，SHA-256 `6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205` |

## 完整性检查

- 写入前设备身份与预期 ESP32-S3 绑定，且当前 App、资源分区和分区表与已验收版本逐字节一致。
- 写入命令只允许 assets 范围，并由 esptool 完成数据哈希校验；未执行整片擦除。
- 写后完整回读与候选分区逐字节一致。
- App 分区完整回读与写入前一致。
- 分区表完整回读与写入前一致。
- 未写入 bootloader、NVS、OTA 数据、语音模型或 C61 底座。
- 受控启动日志约 25 秒完成 LCD、触摸、音频、双麦 DOA、底座 UART 和 MCP 工具初始化，未出现崩溃、断言或看门狗重启标记。

启动日志中已有的 `icon_speaker` / `icon_tips` 缺失提示没有在 E1.18 改动，也不把它们误判为本轮播放器或资源包回归；它们属于此前基线中已记录的资源引用边界。

## 验收边界与仍待决定的事项

用户已在 E1.18 写入和完整回读后确认本体显示、菜单、对话/菜单切换、摸头、睡眠/唤醒等回归正常，记录见 [E1.18 本体回归验收](e1-18-acceptance-20261024.md)。这项回归接受的是运行状态，不代表用户已比较并选择 `surprised` 的动画资源。E1.22 现在提供 `shocked.eaf` 与 `Happy.eaf` 的逐帧对照；具体保留当前映射还是采用候选，仍由用户决定。若选择候选，后续部署还需单独授权，并再次执行实机回归。

底座维修后的 C61、磁场、滑动开关和动作事件仍需另行验收。

如果实机回归失败，回退范围仍只需写回同一 assets 分区的 E1.13 完整镜像，不触碰 App、NVS 或 C61。

## English summary

E1.18 deployed only the body assets partition on the accepted O2f2/E1.13 device. The sole semantic mapping change is `surprised: Happy.eaf → shocked.eaf`; all EAF payloads, the player, App, NVS and C61 remain unchanged. The full assets readback is byte-exact, and the App and partition table are byte-exact unchanged. A short boot capture passed core initialization without panic/watchdog markers. The user accepted the body interaction regression after E1.18; the exact visual choice between `shocked.eaf` and `Happy.eaf` remains open. Base validation remains separate.
