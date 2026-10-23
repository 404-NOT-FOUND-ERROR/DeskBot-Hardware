#ifndef BASE_EVENT_JOURNAL_H
#define BASE_EVENT_JOURNAL_H

#include <cstdint>
#include <type_traits>
#include <freertos/FreeRTOS.h>
#include <freertos/portmacro.h>
#include "base_event_mapper.h"

// Fixed memory side channel; no parser, allocation, task, UI or persistence.
// Busy writes are dropped. Busy/invalid reads leave the output unchanged.
class BaseEventJournal {
public:
    static constexpr uint8_t kCapacity = 24;
    static constexpr uint8_t kRecentCapacity = 6;
    static constexpr uint8_t kMaxStoredData = 8;
    enum : uint8_t {
        kFlagSynthetic = 1u << 0,
        kFlagDataTruncated = 1u << 1,
        kFlagHasValue = 1u << 2,
        kFlagKnown = 1u << 3,
    };

    struct Event {
        uint32_t sequence = 0;  // modulo 2^32, including zero after wrap
        uint32_t timestamp_ms = 0;  // low 32 bits of uptime
        uint16_t value = 0;
        uint8_t command = 0;
        uint8_t data_length = 0;  // original length (0..249)
        uint8_t stored_length = 0;
        uint8_t frame_length = 0;  // zero: no complete frame retained
        uint8_t checksum = 0;  // command + ALL original data, modulo 256
        uint8_t flags = 0;
        base_event_mapper::EventType type = base_event_mapper::EventType::Unknown;
        uint8_t data[kMaxStoredData]{};
        uint8_t frame[kMaxStoredData + 6]{};
    };

    struct State {
        uint64_t last_heartbeat_ms = 0;
        uint32_t total_recorded = 0;  // modulo 2^32
        uint16_t mechanical_status = 0;
        uint8_t size = 0;
        bool online = false;
        bool heartbeat_valid = false;
        bool mechanical_status_valid = false;
    };

    struct RecentSnapshot {
        State state{};
        uint8_t retained_count = 0;
        uint8_t returned_count = 0;
        Event events[kRecentCapacity]{};  // oldest-to-newest
    };

    // Observe only parser-validated payloads AFTER official command handling.
    // Heartbeats and valid magnetic samples are ignored here.
    bool Observe(uint8_t command, const uint8_t* data, int length, uint64_t now_ms);

    // Mirror the official heartbeat/time-out paths, never decide a timeout.
    // Use the official 64-bit last_heartbeat_time_ as the token. This prevents
    // a delayed disconnect from overwriting a newer heartbeat, and vice versa.
    bool ObserveHeartbeat(uint64_t heartbeat_ms, uint64_t now_ms);
    bool NotifyDisconnected(uint64_t expired_heartbeat_ms, uint64_t now_ms);

    bool TryReadState(State* output) const;
    // Oldest-to-newest index. Each read is atomic; multiple reads are NOT one
    // snapshot. A future reader must check sequences if the ring changes.
    bool TryCopy(Event* output, uint8_t index) const;

    // Copy bounded recent state under one zero-wait critical section.
    bool TryReadRecent(RecentSnapshot* output) const;

private:
    bool TryEnter() const;
    void Exit() const;
    static Event Prepare(base_event_mapper::MappedEvent mapped, uint8_t command,
                         const uint8_t* data, int length, uint64_t now_ms);
    // Caller holds mux_; only fixed-size copy and scalar updates here.
    void Commit(Event& event);

    mutable portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
    Event events_[kCapacity]{};
    State state_{};
    uint64_t disconnected_through_ms_ = 0;
    uint8_t next_index_ = 0;
    bool disconnect_valid_ = false;
};

static_assert(std::is_trivially_copyable<BaseEventJournal::Event>::value,
              "Event must remain a fixed copy");
static_assert(sizeof(BaseEventJournal::Event) <= 48, "Event RAM budget exceeded");
static_assert(sizeof(BaseEventJournal) <= 1280, "Journal RAM budget exceeded");
static_assert(sizeof(BaseEventJournal::RecentSnapshot) <= 360,
              "Recent snapshot budget exceeded");

#endif  // BASE_EVENT_JOURNAL_H
