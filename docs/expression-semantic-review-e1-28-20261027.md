# E1.28 表情语义与动画关系审查 / Expression Semantic Review

E1.28 将当前运行时观察摘要与源码静态关系、官方 EAF 图集和动作请求合并成一个只读审查页：[打开 E1.28 工作台](../artifacts/expression-e1-28-semantic-review-20261027/index.html)。本阶段只覆盖 `happy`、`embarrassed`、`sleepy`、`neutral` 四类常见语义，目标是回答“从哪里进入、调用什么、使用哪个 EAF、是否请求动作”，不把这些关系误写成 LCD 或电机已完成事实。页面中的节点图明确把 `SetEmotion` 后的 EAF 播放输入和动作请求画成两条独立支路，没有串行完成假设。

## 当前确认的关系

| 语义 | 入口/边界 | EAF | 动作请求 |
| --- | --- | --- | --- |
| `happy` | 配对检测可静态进入；`llm.emotion` 也可动态进入 | `Happy.eaf` | `shark_head` |
| `embarrassed` | 摸头三次只调用 `WakeWordInvoke("我在摸你猫头")`；服务端返回 emotion 后才可能进入 | `Happy.eaf` | `cat_nuzzle` |
| `sleepy` | 当前源码中作为动态 `llm.emotion` 入口审查；不等同于电源休眠 | `sleep.eaf` | `shark_head_decay` |
| `neutral` | Unknown/Idle、Connecting、Listening 状态分支 | `winking.eaf`，单次 | 无固定动作 |

`Happy.eaf` 的共享只说明两个语义共用同一资源，不说明触发条件或动作相同。`SetPowerSaveMode` 与 `sleepy` 分开处理；`listen.eaf` 状态条也不并入 `neutral` 的眼睛资源。

## 证据边界

- 页面内的圆屏画面是官方 EAF 解码帧的参考合成，不是设备 LCD 实拍。
- `SetEmotion` 和 `vocat_base_control_set_action` 只证明代码请求路径；EAF 播放输入与动作请求是独立支路，不证明 EAF 成功加载、LCD 刷新、C61 收到或动作完成。
- 用户提供的“最近 24 条”是语音口述摘要，只有定性分布，没有原始 JSON、精确次数、顺序或丢弃计数；它不能支持删除资源或改变映射。
- 三个嵌入 PNG 图集已在生成器中解码、验证 PNG 签名并与 E1.19 的 SHA-256 比对；五个源文件以固定 SHA-256 作为证据锚点，发生漂移时生成器失败关闭。
- 本阶段不访问串口、不连接设备、不刷写、不修改固件、NVS、C61 或播放器。报告中的 `deployment_eligible` 和 `flash_authorized` 保持 `false`。

## 验证

```powershell
python tests/test-expression-e1-28-semantic-review.py
python scripts/build-expression-e1-28-semantic-review.py --check
node tests/expression-e1-28-browser.cjs
python scripts/check-workbenches.py
python scripts/check-publication.py
```

Edge 烟测覆盖四张语义卡片、四列节点关系、两条支路的 8 个分支节点、首次异步图集绘制、逐帧/播放/速度控制、响应式宽度和无网络请求。真实顺序与次数仍应由程序保存 E1.26 MCP 原始 JSON 后再导入 E1.27/E1.28。

## 后续闸门

先保留当前 `surprised → shocked.eaf` 和全部官方资源；取得真实快照后，再按单一语义提出映射草案，经过共享引用、播放器、容量、精确回退和用户视觉选择，最后才考虑独立刷写与实机回归。底座修复后的固定布局 A/B/C 可靠性验证和客户端只读身体事实出口仍是更高优先级的独立阶段。
