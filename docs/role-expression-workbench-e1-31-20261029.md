# E1.31 多角色表情草稿工作台 / Offline Role Expression Workbench

更新：2026-10-29。E1.31 是桌面端离线原型，用于验证“角色 profile → 意图 → 表情语义 → EAF 资源 → 圆屏参考预览”能否按角色分开编辑、审阅和往返保存。

## 可运行产物

- [自包含工作台](../artifacts/role-expression-workbench-e1-31-20261029/index.html)
- [生成报告](../artifacts/role-expression-workbench-e1-31-20261029/report.json)
- [草稿协议 JSON Schema](../protocol/role-expression-draft-v1.schema.json)
- [Python 上下文验证器](../protocol/role_expression_draft.py)
- [构建器](../scripts/build-role-expression-workbench-e1-31.py)

工作台从已验收 E1.18 资源快照建立 29 个语义节点和 18 个官方 EAF 资源目录，并提供两个明确标作合成草稿的演示角色。用户可以复制角色、编辑草稿显示名、切换语义资源、断开资源、撤销/重做、恢复当前角色的 E1.18 映射、导入和导出 draft JSON。E1.18 映射与资源内容是只读锚点；更改只影响当前浏览器内存中的角色草稿。圆屏预览沿用 E1.19 的原像素双眼镜像/聆听布局；角色 profile 的动作意图与 E1.18 语义伴随动作分开标注，避免把草稿意图误读为设备执行。

## 数据与边界

- 一个草稿可包含 1–16 个角色；每个 profile 必须保持 `binding_state=draft`，不能表示外壳已经登记或角色已经激活。
- `semantic_resources[semantic] = null` 是合法的“未接线”状态；预览明确显示没有资源。
- 非空资源只能从对应布局允许的 E1.18 官方 EAF 白名单选择。`listen_anim` 只接受 `listen.eaf`；眼部语义不接受 listen 资源。
- 角色的意图映射与语义资源映射分开保存。角色 profile 的动作字段仍受协议允许列表约束；它表达草稿里的动作意图，不代表电机被调用或完成。
- JSON 导入需要匹配完整 E1.18 基线哈希、版本和语义目录。未知字段、非法身份、重复角色 ID、越界大小或不兼容资源会被拒绝，拒绝时当前状态保持不变。
- 导出的 `deskbot.role_expression_draft/v1` 是离线草稿，不是 EAF 包、固件镜像或 NVS 内容；它不能直接刷写或激活设备。
- 页面使用官方解码图集作为参考预览，圆形裁切是浏览器合成，不是实机 LCD 截图。

本阶段不访问串口/设备，不修改 App、C61、资源分区、播放器、官方触摸、DOA、睡眠/唤醒、动作算法或 NVS；不连接 MCP 或 DeepSeek/TTS 客户端。

## 检查与通过条件

执行 `python scripts/build-role-expression-workbench-e1-31.py --check`、`python -m unittest tests/test-role-expression-workbench-e1-31.py -v`、`python scripts/check-workbenches.py`、`python scripts/check-publication.py`、`python scripts/run-offline-checks.py` 和 `git diff --check`。Edge 烟测另以 `node tests/role-expression-workbench-e1-31-browser.cjs` 验证页面交互、JSON 往返、非法导入拒绝、无网络请求、窄屏布局，以及 E1.19 双眼原像素预览锚点和动作意图边界。

## 本轮完成记录

- 生成器 `--check` 通过，产物自洽；HTML 约 3.37 MB，包含 29 个语义、18 个官方 EAF 和 2 个合成角色草稿。
- E1.31 Python 专项测试：5 项通过；Edge 烟测：通过（复制/断开、JSON 往返、撤销/重做、基线恢复、非法导入拒绝、窄屏、无网络/运行时错误、原像素双眼绘制锚点）。
- `check-workbenches.py`：14 个页面、19 个 JS、14 个 JSON payload，通过；`check-publication.py`：1418 个文件，通过；`git diff --check`：通过。
- `run-offline-checks.py`：239 项，237 项通过、2 项跳过。跳过项是尚无真实 F1 导出和本机缺少官方资源能力报告；没有把它们当作通过。
- 本轮未访问串口/MCP/设备，未修改 App、C61、资源分区、NVS，也未生成可刷写包。

离线检查通过表示编辑器和草稿协议通过本阶段验证，不等于资源发布、固件构建、设备写入或实机验收。进入客户端或设备前，仍要分开完成角色/资源契约审计、候选资产兼容性与完整容量/回退审计，再安排最小范围的部署和用户回归。
