#include "base_event_policy.h"

#include <cstring>

bool BaseEventPolicy::Resolve(const base_event_mapper::MappedEvent& event,
                              int64_t timestamp_ms,
                              BaseEventReaction* reaction)
{
    if (reaction == nullptr) {
        return false;
    }
    *reaction = BaseEventReaction{};

    if (!event.known || event.ignored || event.type == nullptr ||
        std::strcmp(event.type, "base.action.completed") != 0) {
        return false;
    }

    if (last_action_completed_ms_ >= 0 &&
        timestamp_ms - last_action_completed_ms_ < kActionCompletedCooldownMs) {
        return false;
    }

    last_action_completed_ms_ = timestamp_ms;
    reaction->kind = BaseEventReaction::Kind::Notification;
    reaction->event_type = event.type;
    reaction->text = "动作完成";
    reaction->duration_ms = 1500;
    return true;
}

void BaseEventPolicy::Reset()
{
    last_action_completed_ms_ = -1;
}
