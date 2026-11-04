E1.40 归档包含写前预检和实机结果两部分。

report.json 与 deployment-manifest.json 是写入前快照。用户确认后已仅向 COM4 写入 bootloader + App，并完成擦除范围回读、保护区核对、启动日志和用户回归。当前结果见 live-result.json 与 docs/expression-capacity-boot-e1-40-live-20261030.md。

本轮未访问 COM8/C61、未擦除全片，未通过刷写触碰 NVS；NVS 有运行时音量设置变化，详情记录于 live-result.json。跨界 CPU read/mmap 和 8 MiB 资源迁移仍待后续阶段验证。
