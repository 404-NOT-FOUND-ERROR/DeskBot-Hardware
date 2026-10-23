# 身体事实协议 v1 / Body Facts

完整 schema 位于 `protocol/deskbot-body-facts-v1.schema.json`。固件/适配器只报告身体事实；客户端拥有角色确认、人格、记忆和音色。当前协议主机实现及回放不意味着实时设备出口已接通。

先 hello、再 snapshot、然后按启动/会话与序号接收增量。缺口、乱序、重连和过期请求新快照。磁场 known 不等于主人确认角色；similarity 与 margin 是实验距离指标，不是概率。

以下为合成示例，不含真实设备标识：

```json
{
  "schema": "deskbot.body.v1",
  "message_type": "snapshot",
  "message_id": "demo-snapshot-1",
  "device_id": "demo-device-a",
  "boot_id": "demo-boot-1",
  "session_id": "demo-session-1",
  "sequence": 1,
  "occurred_at": "2026-10-18T04:05:10.123Z",
  "payload": {
    "kind": "snapshot",
    "base": {"link": "online", "mechanical_status": "ready", "last_event_sequence": 0},
    "event_cursor": {"oldest_available_sequence": null, "high_watermark_sequence": 0, "gap_detected": false},
    "magnetic": {"state": "fresh", "sensor_kind": "BMM150", "x": 190, "y": -540, "z": -940, "sample_at": "2026-10-18T04:05:10.100Z", "age_ms": 23},
    "shell_observation": {"status": "known", "profile_id": "demo-profile-a", "profile_revision": 1, "sensor_kind": "BMM150", "similarity": 0.12, "margin": 0.31, "sample_count": 8, "observed_at": "2026-10-18T04:05:10.100Z"}
  }
}
```
