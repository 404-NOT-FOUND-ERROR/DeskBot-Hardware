#pragma once
#include "studio_role_graph.h"
#include "gfx.h"
#include "studio_runtime_snapshot.h"

namespace deskbot_role {
enum class Block : uint8_t { Menu = 1, Listening = 2, Sleep = 4 };
// Read the synchronized owner snapshot before acquiring the Emote lock. Unknown
// or transitioning channels pause until a subsequent settled snapshot arrives.
inline uint8_t OwnerBlockers(const deskbot_studio_runtime::Snapshot& snapshot) {
    using deskbot_studio_runtime::Page;
    constexpr auto settled = deskbot_studio_runtime::Phase::Settled;
    uint8_t result = 0;
    const auto& page = snapshot.page;
    const auto& app = snapshot.application;
    if (!page.revision || page.phase != settled || page.value != static_cast<uint8_t>(Page::Home))
        result |= static_cast<uint8_t>(Block::Menu);
    if (!app.revision || app.phase != settled || app.value > kDeviceStateFatalError ||
        !deskbot_studio_runtime::KnownApplication(static_cast<DeviceState>(app.value)) ||
        app.value == kDeviceStateListening)
        result |= static_cast<uint8_t>(Block::Listening);
    return result;
}
using Schedule = void (*)(uint64_t ticket);
// The mount owner supplies current blockers even after the old runtime was reset.
LoadResult Configure(emote_handle_t handle, Schedule schedule, uint8_t initial_blocked);
void Reset(emote_handle_t handle);
bool Request(emote_handle_t handle, const char* semantic, esp_err_t& result);
// Explicit playback events may replay the same semantic; status refreshes use Request.
bool Replay(emote_handle_t handle, const char* semantic, esp_err_t& result);
bool Cancel(emote_handle_t handle);
bool Select(emote_handle_t handle, const char* role_id);
void SetBlocked(emote_handle_t handle, Block reason, bool value);
void SyncOwners(emote_handle_t handle, uint8_t owner_blocked);
void OnFrame(emote_handle_t handle, gfx_player_event_t event, const void* object);
void Drain(emote_handle_t handle, uint64_t ticket);
void Poll(emote_handle_t handle);
// Scalar snapshot is useful to future activation/reporting without exposing mmap pointers.
struct Snapshot { bool loaded = false; uint8_t selected = 0, blocked = 0; Phase phase = Phase::None; uint16_t cursor = 0; };
Snapshot Read(emote_handle_t handle);
Details Describe(emote_handle_t handle);
}  // namespace deskbot_role
