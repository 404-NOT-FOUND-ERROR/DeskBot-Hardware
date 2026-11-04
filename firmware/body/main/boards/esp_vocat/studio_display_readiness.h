#pragma once

#include "studio_runtime_query.h"
#include <cstring>

namespace deskbot_studio_display {
enum class Player : uint8_t { Unknown, Unavailable, Initialized, Stopping };

// Written only at the display owner's construction/destruction boundaries.
// A valid player handle establishes initialization, not LCD visibility/sleep.
struct PlayerState {
    uint32_t revision = 0;
    Player value = Player::Unknown;
};

class Store {
public:
    explicit constexpr Store(uint32_t revision = 0) : state_{revision, Player::Unknown} {}
    void Observe(Player player) {
        if (state_.revision == UINT32_MAX) {
            state_.value = Player::Unknown;
            return;
        }
        ++state_.revision;
        state_.value = player;
    }
    PlayerState Read() const { return state_; }
private:
    PlayerState state_{};
};

struct Snapshot {
    deskbot_studio_runtime::Snapshot runtime{};
    PlayerState player{};
};
using Provider = Snapshot (*)();

inline const char* PlayerName(PlayerState player) {
    if (!player.revision) return "unknown";
    switch (player.value) {
        case Player::Unavailable: return "unavailable";
        case Player::Initialized: return "initialized";
        case Player::Stopping: return "stopping";
        default: return "unknown";
    }
}

inline const char* Reason(const Snapshot& snapshot) {
    using namespace deskbot_studio_runtime;
    const char* player = PlayerName(snapshot.player);
    if (!std::strcmp(player, "unknown")) return "player_unknown";
    if (snapshot.player.value == Player::Unavailable) return "player_unavailable";
    if (snapshot.player.value == Player::Stopping) return "player_stopping";
    const auto& app = snapshot.runtime.application;
    const auto& page = snapshot.runtime.page;
    if (!app.revision || app.phase == Phase::Unknown ||
        (app.phase != Phase::Transitioning && app.phase != Phase::Settled) ||
        (app.phase == Phase::Settled && !deskbot_studio_runtime_query::ApplicationValue(app.value)))
        return "application_unknown";
    if (app.phase == Phase::Transitioning) return "application_transitioning";
    if (app.value != kDeviceStateIdle) return "application_busy";
    if (!page.revision || page.phase == Phase::Unknown ||
        (page.phase != Phase::Transitioning && page.phase != Phase::Settled) ||
        (page.phase == Phase::Settled && page.value != static_cast<uint8_t>(Page::Home) &&
         page.value != static_cast<uint8_t>(Page::Other))) return "page_unknown";
    if (page.phase == Phase::Transitioning) return "page_transitioning";
    if (page.value != static_cast<uint8_t>(Page::Home)) return "not_home";
    // The current public player API provides no actual sleep/awake signal.
    return "sleep_unknown";
}

inline bool Format(const char* request, const char* target, const char* boot,
                   const char* session, uint32_t sequence, uint64_t at_ms,
                   uint32_t lease_ms, const Snapshot& snapshot,
                   char* output, size_t capacity) {
    if (!output || capacity < 1024) return false;
    char application[96], page[96], time[21];
    deskbot_studio_runtime_query::Channel(snapshot.runtime.application, true, application, sizeof(application));
    deskbot_studio_runtime_query::Channel(snapshot.runtime.page, false, page, sizeof(page));
    deskbot_studio_link::Decimal64(at_ms, time);
    const int count = std::snprintf(output, capacity,
        "{\"schema\":\"deskbot.studio_display/v1\",\"message_type\":\"display\","
        "\"request_id\":\"%s\",\"target_id\":\"%s\",\"boot_id\":\"%s\","
        "\"sequence\":%" PRIu32 ",\"session_id\":\"%s\",\"at_ms\":%s,\"lease_ms\":%" PRIu32 ","
        "\"application\":%s,\"page\":%s,\"sleep\":\"unknown\",\"controls_available\":false,"
        "\"player\":{\"revision\":%" PRIu32 ",\"state\":\"%s\"},"
        "\"preview\":{\"available\":false,\"reason\":\"%s\"}}",
        request, target, boot, sequence, session, time, lease_ms, application, page,
        snapshot.player.revision, PlayerName(snapshot.player), Reason(snapshot));
    return count > 0 && static_cast<size_t>(count) < capacity;
}

static_assert(sizeof(Store) <= 8, "bounded player state");
static_assert(sizeof(Snapshot) <= 32, "bounded display snapshot");
}  // namespace deskbot_studio_display
