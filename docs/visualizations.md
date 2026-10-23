# 可视化导览 / Visualization Guide

[中文仓库介绍](../README.md) · [English overview](../README.en.md)

## 打开工作台

以下页面自包含，不需要 npm、网络或设备，GitHub 文件页本身不运行 HTML：

```powershell
python -m http.server 8080 --bind 127.0.0.1
```

在浏览器打开 `http://127.0.0.1:8080/artifacts/expression-node-workbench-e1-4d-20261021/index.html`，或直接双击相应 `index.html`。本地服务器仅绑定回环地址，不作为客户端实时连接服务。

| 页面 | 用途 |
| --- | --- |
| [节点工作台](../artifacts/expression-node-workbench-e1-4d-20261021/index.html) | 选择行为，查看语义/调用/EAF/布局/动作的节点关系和圆屏预览；官方映射与候选覆盖分层 |
| [绑定矩阵](../artifacts/expression-binding-matrix-e1-9-20261022/index.html) | 统计复用、入口和待核对资源，作为清理/替换决策前的证据 |
| [压缩审阅](../artifacts/expression-e1-10-review-20261022/index.html) | 对照候选和资源压缩，检查画布、视觉和预算 |

工作台主要 UI 为中文，仓库介绍可切换中英。没有承诺工作台本身已完成全界面国际化。

![表达节点与发布链路原创示意](images/expression-pipeline.svg)

上图为原创架构示意，不是运行截图。公开工作台保留官方资源的圆屏预览；外部害羞研究候选的素材再分发许可尚未确认，因此只保留元数据和节点关系，画面位置显示明确占位，不包含候选图像或派生截图。原始本机研究文件不受影响。

## 如何解释图

- 图展示源码审计得到的静态关系，不是串口实时日志或设备动作轨迹。
- 多个语义可共享 `Happy.eaf`；它们的触发上下文不因此相同。
- 眼睛资源可通过布局镜像/裁切组成完整圆屏，文件尺寸不等于屏幕尺寸。
- 动作分支是可视化关联，不会驱动电机；离线候选不代表已部署。
- 害羞候选仅保留研究比较元数据；E1.13 实际部署仅为 `insert/eat` 无损重编码。

The original architecture SVG and self-contained pages are publication artifacts, not evidence of a new browser interaction run or hardware playback acceptance. Historical report hashes identify the archived inputs; publication-only navigation and media withholding change the public HTML bytes.
