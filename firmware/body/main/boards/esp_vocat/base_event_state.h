#ifndef DESKBOT_BASE_EVENT_STATE_H
#define DESKBOT_BASE_EVENT_STATE_H

#include <cstdint>

// Pure state transitions used by BaseControl. The caller owns any required
// synchronization; keeping this class transport-free makes the de-duplication
// rules testable without ESP-IDF or a UART device.
class BaseEventStateTracker {
public:
    void Reset();

    // Returns true only for an offline -> online transition.
    bool HandleHeartbeat(int64_t timestamp_ms);

    // Returns true only when an online base crosses the timeout threshold.
    bool CheckHeartbeatTimeout(int64_t timestamp_ms, int64_t timeout_ms);

    // Returns true only when the mechanical status changes or is first known.
    bool UpdateMechanicalStatus(uint16_t status);

    bool online() const { return online_; }
    int64_t last_heartbeat_ms() const { return last_heartbeat_ms_; }
    bool mechanical_status_valid() const { return mechanical_status_valid_; }
    uint16_t mechanical_status() const { return mechanical_status_; }

private:
    bool online_ = false;
    int64_t last_heartbeat_ms_ = 0;
    bool mechanical_status_valid_ = false;
    uint16_t mechanical_status_ = 0;
};

#endif  // DESKBOT_BASE_EVENT_STATE_H
