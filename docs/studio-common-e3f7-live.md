# E3f-7：八种表情实机安装

更新：2026-11-11。COM4 已安装 E3f-7 App，继续使用 E1.45 资源。工作台为 [8777](http://127.0.0.1:8777/#connection)。

## 已完成

| 检查 | 结果 |
| --- | --- |
| 安装与恢复工具 | 32 项测试、独立 AI 复核通过 |
| App | 4,521,872 B；完整擦除范围回读一致，启动身份和五条周期消息通过 |
| 设置与资源 | 写后首次启动前 NVS 一致，其余 Flash 摘要一致；现场快照已保存 |
| 工作台连接 | 37 项真实目录与 E1.45 一致，八项能力齐全 |
| 混合请求 | 八种表情加末次进食共九次，均收到同一请求的 `accepted` 与 `dispatched`；连接、启动与会话一致 |
| 圆屏与日常交互 | 等主人方便时验收 |

八种入口为平静、害羞、进食、开心、难过、困倦、困惑、惊讶。进食沿用完整画面，其余使用登记的眼部布局；附带动作沿用资源目录。困倦表情与系统睡眠分别操作。

## 主人怎么验收

1. 打开工作台，让桌宠回到主页并结束对话。设备已连接，若重新插拔过，选择 COM4 连接并启用状态查询。
2. 依次播放进食 → 平静 → 害羞 → 开心 → 难过 → 困倦 → 困惑 → 惊讶 → 进食，每次观察约四秒。检查画面完整、进食无镜像、害羞双眼正常。
3. 菜单往返五次；说一句话，在它说话时和说完后分别切菜单；摸头三次；睡眠后再唤醒。
4. 完成后统一反馈正常或具体异常步骤。版本保持运行，可按自己的时间测试。

## 恢复与接续

本轮计划 `6aefc43255be0f81337c32dc06eee4b78e56e909d68437ca435aefca0985d9d4`，App 写入范围 `0x10000..0x460000`。恢复使用本轮原字节返回已验收的 E3f-5，保留恢复时的 NVS 和 E1.45。

先停止工作台 USB 观察，再用已核对的 IDF Python 3.11 执行：

```powershell
& 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' scripts/deploy-studio-common-e3f7.py recover --run e3c-live-20261111-e3f7-live1
```

接下来依次完成：本版验收 → 素材布局随包发布 → 独立动作与组合 → 外壳与角色 → 角色对话。

记录：[设计](studio-common-e3f7-installation.md) · [部署测试](../artifacts/studio-common-e3f7/deployment-validation-1.json) · [独立复核](../artifacts/studio-common-e3f7/deployment-peer-review.json) · [现场结果](../artifacts/studio-common-e3f7/live-result.json) · [九次回执](../artifacts/studio-common-e3f7/live-dispatches.json)。
