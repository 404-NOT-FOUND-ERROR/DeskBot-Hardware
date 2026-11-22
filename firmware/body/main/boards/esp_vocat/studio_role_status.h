#pragma once
#include <cstddef>
#include <cstdint>

namespace deskbot_role {
enum class Phase : uint8_t { None, Single, Entry, Loop, Exit, Held };
constexpr uint8_t kNoRole = 255;
// A bounded copy of mounted facts. Resource pointers never leave the player.
struct Details {
    char graph_sha256[65]{}, roles[4][65]{};
    uint64_t selection = 0, rendered_selection = 0;
    uint8_t count = 0, initial = kNoRole, target = kNoRole;
    uint8_t current = kNoRole, rendered = kNoRole, blocked = 0;
    Phase phase = Phase::None;
    bool loaded = false, sleep_known = false, asleep = false, failed = false;
};
}  // namespace deskbot_role
