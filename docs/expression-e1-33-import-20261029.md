# E1.33 新动画导入与兼容性审计

E1.33 将一份来源明确的 Espressif `shy` 动画作为**尚未接线的离线候选**，核对文件结构、官方 C 播放器、圆屏布局、局部循环边界和 E1.18 资源分区预算。阶段结果是：格式与两种布局的 C 渲染通过；容量和产品映射表达能力仍阻止部署。

## 结果和页面

- [公开离线工作台](../artifacts/expression-e1-33-import-20261029/index.html)：显示当前 `embarrassed → Happy.eaf` 边与未接线候选、候选结构、播放器/计时器检查和容量结论。候选媒体和候选截图不嵌入公开副本，因为素材再分发许可未确认。
- 本机候选预览页：`tmp/expression-e1-33-import-20261029-preview/index.html`。它是本机忽略的产物，含候选逐帧播放、整段/局部循环和两种 C 播放器关键帧图集；公开 clone 不附带这份媒体。
- [公开机器报告](../artifacts/expression-e1-33-import-20261029/report.json)：带源哈希、完整 C 逐帧摘要、循环计时证据、负向输入拒绝和容量算术；不含可刷写包。
- 生成器：[audit-expression-e1-33-import.py](../scripts/audit-expression-e1-33-import.py)、[build-expression-e1-33-import.py](../scripts/build-expression-e1-33-import.py)。
- 测试：[test-expression-e1-33-import.py](../tests/test-expression-e1-33-import.py)、[expression-e1-33-import-browser.cjs](../tests/expression-e1-33-import-browser.cjs)。

本机原始研究输入是 `Emote-Pack.zip` 的 `gif/shy_[2.0s, 4.0s].gif` 以及对应 EAF。报告锚定：EAF SHA-256 `467db1c5c912b4a7d0c2c280f3eaf158fb3c9e0fe633e2c7e47ad4b848b78293`；GIF SHA-256 `840e028a34cf8527a05a6dc264a15bede32542478c27180ce2b8584393c50be8`；ZIP SHA-256 `946654de50253949b2c687a2b6b53bf3b66fdd699ab107950258e2a830cf15e6`。

## 核验结论

- 候选为 360×360、8-bit、188 个图像帧，块编码 `[0,1,3]`；GIF 源为 848×848、188 帧，所有帧间隔 4 cs，对应 25 FPS。
- 通过当前锚定的 Espressif C 解码/渲染器检查：full-screen 与 `eye_anim` 自动镜像布局共 376 次逐帧比较，均为零像素差，保护区和释放检查通过。`eye_anim` 的通过只证明解码/绘制正确；候选画面仍是完整脸，不应直接认定适合作为单眼资源。
- C 计时器的 `[50,150)` 段循环通过。官方产品 `emote.json` 只有整段 `loop`/`fps` 字段，不能直接记录此局部循环端点；所以通过 C 层检查不能推导当前产品配置能配置同样的局部循环。
- E1.18 assets 分区为 6,144,000 B；原包 5,538,351 B，空余 605,649 B。原样追加 717,402 B 候选后超出 111,606 B；替换 `Happy.eaf` 方案仍超 30,108 B，替换 `shocked.eaf` 仍超 46,790 B。这三种是本轮明确计算的方案，不能泛化为所有资源替换均不可能。`Happy.eaf` 同时被 9 个语义引用，不应为了空间直接替换它。
- 七种负向样本（校验错误、截断、倒置/越界循环、无效 FPS、尺寸错、未知编码）均被拒绝。

## 边界

结果 `offline_audit_complete_deployment_blocked`。本轮无串口/设备访问，不改变固件、C61、NVS 或正式映射；没有重打资源包或制作可刷写产物。公开页省略未授权再发布的候选画面；需要在本机的私有研究页审看候选。主机 C 播放器结果不是圆屏实机验收。

## 可复现检查

```powershell
python .\scripts\audit-expression-e1-33-import.py --check --output .\tmp\expression-e1-33-import-20261029-final
python .\scripts\build-expression-e1-33-import.py --check
python .\scripts\build-expression-e1-33-import.py --check --local
python .\tests\test-expression-e1-33-import.py
node .\tests\expression-e1-33-import-browser.cjs
```

## 下一阶段

E1.34 先做候选预处理实验，不动设备：保留原始 GIF/EAF 哈希作为母稿，比较去重复帧/无损重编码、受控尺寸或帧率变化对体积和视觉的影响；并在工作台明确选择完整脸 full-screen 或裁切后的眼部布局、整段循环或引入配置字段表达局部循环。每个候选都要重跑 EAF 结构、官方 C 全帧、内存保护、计时器、包容量和 E1.18 精确回退审计。视觉和预算都通过后再讨论单一语义接线，另行取得素材许可与刷写授权；不把 E1.33 的主机兼容性当部署许可。
