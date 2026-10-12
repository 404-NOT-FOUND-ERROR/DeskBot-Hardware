#include "base_event_state.h"

void BaseEventStateTracker::Reset()
{
    online_ = false;
    last_heartbeat_ms_ = 0;
    mechanical_status_valid_ = false;
    mechanical_status_ = 0;
}

bool BaseEventStateTracker::HandleHeartbeat(int64_t timestamp_ms)
{
    const bool just_connected = !online_;
    online_ = true;
    last_heartbeat_ms_ = timestamp_ms;
    return just_connected;
}

bool BaseEventStateTracker::CheckHeartbeatTimeout(int64_t timestamp_ms,
                                                  int64_t timeout_ms)
{
    if (!online_ || last_heartbeat_ms_ == 0 || timeout_ms < 0) {
        return false;
    }
    if (timestamp_ms - last_heartbeat_ms_ <= timeout_ms) {
        return false;
    }
    online_ = false;
    return true;
}

bool BaseEventStateTracker::UpdateMechanicalStatus(uint16_t status)
{
    const bool changed = !mechanical_status_valid_ || mechanical_status_ != status;
    mechanical_status_valid_ = true;
    mechanical_status_ = status;
    return changed;
}
