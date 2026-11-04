#pragma once

#include "device_state.h"
#include <cstddef>
#include <cstdint>

namespace deskbot_studio_runtime {
enum class Phase : uint8_t { Unknown, Transitioning, Settled };
enum class Page : uint8_t { Unknown, Home, Other };
enum class Sleep : uint8_t { Unknown };

struct State {
    uint32_t revision = 0;
    uint8_t value = 0;
    Phase phase = Phase::Unknown;
};

// Caller supplies synchronization. Tickets pair each owner's begin/end, with
// four simultaneous writers. Overlap leaves the result unknown until the next
// uncontended transition; it never guesses which owner's side effects won.
class Channel {
public:
    explicit constexpr Channel(uint32_t revision = 0) : state_{revision, 0, Phase::Unknown} {}

    uint32_t Begin() {
        if (faulted_) return 0;
        size_t slot = 0;
        while (slot < kWriters && tickets_[slot]) ++slot;
        if (slot == kWriters || state_.revision == UINT32_MAX) {
            Fault();
            return 0;
        }
        if (active_ == 0) overlap_ = false;
        else overlap_ = true;
        tickets_[slot] = ++state_.revision;
        ++active_;
        state_.value = 0;
        state_.phase = Phase::Transitioning;
        return tickets_[slot];
    }

    void End(uint32_t ticket, uint8_t value, bool valid) {
        if (faulted_) return;
        size_t slot = 0;
        while (slot < kWriters && tickets_[slot] != ticket) ++slot;
        if (!ticket || slot == kWriters) {
            Fault();
            return;
        }
        tickets_[slot] = 0;
        --active_;
        if (active_) return;
        state_.phase = !overlap_ && valid ? Phase::Settled : Phase::Unknown;
        state_.value = state_.phase == Phase::Settled ? value : 0;
    }

    State Read() const { return state_; }

private:
    void Fault() {
        faulted_ = true;
        state_.value = 0;
        state_.phase = Phase::Unknown;
    }
    static constexpr size_t kWriters = 4;
    State state_{};
    uint32_t tickets_[kWriters]{};
    uint8_t active_ = 0;
    bool overlap_ = false;
    bool faulted_ = false;
};

inline bool KnownApplication(DeviceState state) {
    switch (state) {
        case kDeviceStateStarting: case kDeviceStateWifiConfiguring:
        case kDeviceStateIdle: case kDeviceStateConnecting:
        case kDeviceStateListening: case kDeviceStateSpeaking:
        case kDeviceStateUpgrading: case kDeviceStateActivating:
        case kDeviceStateAudioTesting: case kDeviceStateFatalError:
            return true;
        default: return false;
    }
}

struct Snapshot {
    State application{};
    State page{};
    Sleep sleep = Sleep::Unknown;
    bool controls_available = false;
};

class Store {
public:
    uint32_t BeginApplication() { return application_.Begin(); }
    void EndApplication(uint32_t ticket, DeviceState state) {
        application_.End(ticket, static_cast<uint8_t>(state), KnownApplication(state));
    }
    uint32_t BeginPage() { return page_.Begin(); }
    void EndPage(uint32_t ticket, Page page) {
        page_.End(ticket, static_cast<uint8_t>(page), page == Page::Home || page == Page::Other);
    }
    Snapshot Read() const { return {application_.Read(), page_.Read(), Sleep::Unknown, false}; }
private:
    Channel application_{};
    Channel page_{};
};

static_assert(sizeof(Store) <= 80, "bounded runtime state store");
static_assert(sizeof(Snapshot) <= 24, "bounded runtime snapshot");
}  // namespace deskbot_studio_runtime
