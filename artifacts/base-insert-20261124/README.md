# 底座连接表演修正

底座离线后重新收到心跳，播放当前 r8 角色的 `insert` 完整单次表演。入口采用 `ReplayEmotion`，确保连续两次重新连接都可播放。其余 33 个编译源码摘要、资源图与 r8 相同。

32 项主机检查及独立复核通过。修正版已安装，App/资源回读、NVS、范围外 Flash 和启动检查通过。小智与身体联网服务已恢复；实屏观察待主人反馈。

公开内容为源码差异和验证摘要；设备专用 App 与现场恢复对留本机。此目录记录 11 月 24 日安装，当前 App 已接续[闹钟修复](../../docs/alarm-management.md)。

安装与后台状态见 `installation.json`；目标构建见 `build-report.json`；源码范围见 `source-delta.json`。现场恢复目录为 `tmp/layered-face-live-20261124-insert/`，运行 `scripts/deploy-layered-base-insert.py recover` 可恢复本次安装前 r8 的 App/资源对。
