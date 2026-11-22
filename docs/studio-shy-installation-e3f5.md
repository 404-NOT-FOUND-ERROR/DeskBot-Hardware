# E3f-5：害羞 App 安装

更新：2026-11-10。E3f-4e + E1.45 已通过主人验收，接续[害羞候选](studio-shy-e3f5.md)。

目标：在工作台播放害羞，同时保留进食、平静和当前日常交互。

## 设计与步骤

接口沿用 `prepare / check / prepare-live / install / recover`。新增版本适配器固定 E3f-5 镜像、双配置构建、测试和复核摘要；事务继续使用现有 App 安装器。

数据流：已验收的 E3f-4e 安装记录 → 重建含 E1.45 的全片参考 → 读取现场前缀与全片摘要核对 → 保存当下 NVS 和回退材料 → 写 App、回读、检查启动 → 工作台重连并读取 37 项目录与三语义能力 → 主人观察。

依赖：E3f-4e 同轮安装记录及主人验收、E3f-5 冻结构建、原 App 事务、当前 E3f-4b 工作台。写入 COM4 的 App 擦除范围为 `0x10000..0x460000`，共 4,521,984 B；资源继续使用 E1.45。恢复精确写回本轮保存的 E3f-4e App，保留运行时 NVS。

检查包括：候选与原版身份、资源和尾扇区、修改过的证据、更新过的 NVS；模拟部分写入、回读和启动失败后的恢复；删除候选或历史参考后仍可用本轮备份恢复。另一位 AI 复核后执行现场安装。

## 验收

工作台依次播放进食、平静、害羞、进食，核对整屏与双眼布局、200 px 害羞画面及原蹭蹭动作；再检查菜单往返、对话中和结束后切菜单、摸头、睡眠唤醒。

后续：E3f-6 节点实机播放 → 常用表情与独立动作 → 外壳和角色闭环。

## 本轮结果

32 项安装与事务测试通过，204 份输入摘要一致；另一位 AI 复核通过。现场计划 `cb837f6c3d731d8aabc193cfa53ed9517cad67cabab00898b839eab6a52c75cd` 已完成 COM4 App 安装，完整擦除范围逐字节回读一致。NVS、其余 Flash、启动身份与周期消息核对通过；工作台重连后 37 项目录一致，三项语义能力齐全。进食→平静→害羞→进食四次设备下发通过，主人已回复“全部正常”，本轮画面、菜单、对话、摸头和睡眠验收通过，见[验收记录](../artifacts/studio-shy-e3f5/manual-acceptance.json)。

使用现有[工作台](http://127.0.0.1:8775/#connection)，刷新后依次播放四次，每次留几秒观察；再测试菜单、对话、摸头和睡眠唤醒。

恢复入口：用预检给出的 IDF Python 3.11 执行 `scripts/deploy-studio-shy-e3f5.py recover --run e3c-live-20261110-e3f5-live1`。先在工作台停止 USB 观察，执行后重新连接。它恢复本轮保存的 E3f-4e App，继续使用 E1.45 和当下 NVS。

记录：[部署测试](../artifacts/studio-shy-e3f5/deployment-validation-1.json) · [部署复核](../artifacts/studio-shy-e3f5/deployment-peer-review.json) · [现场结果](../artifacts/studio-shy-e3f5/live-result.json) · [目录](../artifacts/studio-shy-e3f5/live-directory.json) · [回执](../artifacts/studio-shy-e3f5/live-dispatches.json)。
