# 发布检查 / Publication Checks

记录日期：2026-10-23。此文件记录公开副本的检查，不等同固件构建或实机验收。

## 本次结果

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `scripts/check-publication.py` | PASS | 公开文件 1218 个；本地 Markdown 链接、JSON、来源清单、文件大小、语言入口、离线 CSP、候选媒体隔离和敏感信息模式检查通过 |
| `scripts/run-offline-checks.py` | PASS | 136 项，134 passed，2 skipped，0 failed；跳过项是未随公开副本提供的完整 F1 导出和完整 EAF 元数据报告 |
| `scripts/check-workbenches.py` | PASS | 3 个页面、3 段内联 JS、2 个 JSON payload；相对链接和回环 HTTP 200/字节一致 |
| C++ 磁场核心语法检查 | PASS | ESP clang `-std=c++17 -Wall -Wextra -Werror -fsyntax-only`；公开环境未执行本地可执行文件 |
| `git diff --check` | PASS | 无空白错误 |

运行环境为 Python 3.11；离线测试的 `jsonschema` 使用 clone-local 临时依赖目录，检查后已删除。本文记录的是公开副本检查；E1.13 的用户实机验收另见 [验收记录](e1-13-acceptance-20261024.md)，不把 Git 检查冒充设备测试。

## 发布边界

- 没有访问串口、刷写设备、擦除 Flash 或修改 NVS。
- O2f2 App 的哈希和 E1.13 资源回读结果来自先前的验收记录；本次只是公开副本审计。
- 乐鑫 Emote Pack 中的害羞候选没有确认可再分发许可证，因此公开副本保留其来源、哈希、容量和审阅结论，但不把该候选的原始/派生媒体作为当前设备资源或产品授权发布。
- 工作台是静态源码关系和主机预览，不是实时运行轨迹；“通过”不代表 LCD 播放、底座运动或整机回归通过。
