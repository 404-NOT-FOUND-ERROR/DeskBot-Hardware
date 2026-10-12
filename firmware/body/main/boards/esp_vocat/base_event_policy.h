#ifndef DESKBOT_BASE_EVENT_POLICY_H
#define DESKBOT_BASE_EVENT_POLICY_H

#include <cstdint>

#include "base_event_mapper.h"

// Describes one additive reaction to an already validated base event.  The
// policy layer deliberately contains no Display, UART, or motor dependency so
// it can be tested without an ESP-IDF build.
struct BaseEventReaction {
    enum class Kind : uint8_t {
        None = 0,
        Notification = 1,
    };

    Kind kind = Kind::None;
    const char* event_type = nullptr;
    const char* text = nullptr;
    int duration_ms = 0;
};

class BaseEventPolicy {
public:
    // Resolve at most one additive reaction for an event.  The first version
    // intentionally enables only action-completed notifications.
    bool Resolve(const base_event_mapper::MappedEvent& event,
                int64_t timestamp_ms, BaseEventReaction* reaction);

    void Reset();

private:
    static constexpr int64_t kActionCompletedCooldownMs = 1200;
    int64_t last_action_completed_ms_ = -1;
};

#endif  // DESKBOT_BASE_EVENT_POLICY_H
