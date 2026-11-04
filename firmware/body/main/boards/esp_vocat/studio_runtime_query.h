#pragma once

#include "studio_runtime_snapshot.h"
#include "studio_device_link.h"
#include <cstdio>
#include <inttypes.h>

namespace deskbot_studio_runtime_query {
using Provider = deskbot_studio_runtime::Snapshot (*)();

inline const char* ApplicationValue(uint8_t value) {
    switch (value) {
        case kDeviceStateStarting: return "starting";
        case kDeviceStateWifiConfiguring: return "wifi_configuring";
        case kDeviceStateIdle: return "idle";
        case kDeviceStateConnecting: return "connecting";
        case kDeviceStateListening: return "listening";
        case kDeviceStateSpeaking: return "speaking";
        case kDeviceStateUpgrading: return "upgrading";
        case kDeviceStateActivating: return "activating";
        case kDeviceStateAudioTesting: return "audio_testing";
        case kDeviceStateFatalError: return "fatal_error";
        default: return nullptr;
    }
}

inline void Channel(const deskbot_studio_runtime::State& state, bool application,
                    char* output, size_t capacity) {
    using deskbot_studio_runtime::Phase;
    using deskbot_studio_runtime::Page;
    const char* value = application ? ApplicationValue(state.value) :
        state.value == static_cast<uint8_t>(Page::Home) ? "home" :
        state.value == static_cast<uint8_t>(Page::Other) ? "other" : nullptr;
    const char* phase = "unknown";
    char json_value[24] = "null";
    if (state.revision && state.phase == Phase::Transitioning) phase = "transitioning";
    else if (state.revision && state.phase == Phase::Settled && value) {
        phase = "settled";
        std::snprintf(json_value, sizeof(json_value), "\"%s\"", value);
    }
    std::snprintf(output, capacity, "{\"revision\":%" PRIu32 ",\"phase\":\"%s\",\"value\":%s}",
                  state.revision, phase, json_value);
}

// Inputs are already validated by the session. Snapshot copying finishes
// before formatting; no Application, LVGL or peripheral operation is called.
inline bool Format(const char* request, const char* target, const char* boot,
                   const char* session, uint32_t sequence, uint64_t at_ms,
                   uint32_t lease_ms, const deskbot_studio_runtime::Snapshot& snapshot,
                   char* output, size_t capacity) {
    if (!output || capacity < 1024) return false;
    char application[96], page[96], time[21];
    Channel(snapshot.application, true, application, sizeof(application));
    Channel(snapshot.page, false, page, sizeof(page));
    deskbot_studio_link::Decimal64(at_ms, time);
    const int count = std::snprintf(output, capacity,
        "{\"schema\":\"deskbot.studio_state/v1\",\"message_type\":\"state\","
        "\"request_id\":\"%s\",\"target_id\":\"%s\",\"boot_id\":\"%s\","
        "\"sequence\":%" PRIu32 ",\"session_id\":\"%s\",\"at_ms\":%s,\"lease_ms\":%" PRIu32 ","
        "\"application\":%s,\"page\":%s,\"sleep\":\"unknown\",\"controls_available\":false}",
        request, target, boot, sequence, session, time, lease_ms, application, page);
    return count > 0 && static_cast<size_t>(count) < capacity;
}
}  // namespace deskbot_studio_runtime_query
