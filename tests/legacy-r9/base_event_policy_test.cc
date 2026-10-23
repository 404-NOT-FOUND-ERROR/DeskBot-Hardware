#include "base_event_policy.h"

#include <cassert>
#include <cstring>

namespace {

base_event_mapper::MappedEvent action_completed()
{
    base_event_mapper::MappedEvent event;
    event.type = "base.action.completed";
    event.value = 0x0010;
    event.has_value = true;
    event.known = true;
    return event;
}

}  // namespace

int main()
{
    BaseEventPolicy policy;
    BaseEventReaction reaction;

    const auto completed = action_completed();
    assert(policy.Resolve(completed, 1000, &reaction));
    assert(reaction.kind == BaseEventReaction::Kind::Notification);
    assert(std::strcmp(reaction.event_type, "base.action.completed") == 0);
    assert(std::strcmp(reaction.text, "动作完成") == 0);
    assert(reaction.duration_ms == 1500);

    // Repeated completion frames inside the cooldown do not spam the UI.
    assert(!policy.Resolve(completed, 1500, &reaction));
    assert(policy.Resolve(completed, 2200, &reaction));

    base_event_mapper::MappedEvent unknown;
    unknown.type = "base.uart.unknown_event";
    unknown.known = false;
    assert(!policy.Resolve(unknown, 4000, &reaction));

    base_event_mapper::MappedEvent ignored = completed;
    ignored.ignored = true;
    assert(!policy.Resolve(ignored, 5000, &reaction));

    policy.Reset();
    assert(policy.Resolve(completed, 5000, &reaction));
    return 0;
}
