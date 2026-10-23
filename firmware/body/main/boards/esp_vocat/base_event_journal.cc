#include "base_event_journal.h"

#include <cstring>

bool BaseEventJournal::TryEnter() const
{
    return portTRY_ENTER_CRITICAL(&mux_, 0) == pdPASS;
}

void BaseEventJournal::Exit() const
{
    portEXIT_CRITICAL(&mux_);
}

BaseEventJournal::Event BaseEventJournal::Prepare(
    base_event_mapper::MappedEvent mapped, uint8_t command,
    const uint8_t* data, int length, uint64_t now_ms)
{
    Event event{};
    event.timestamp_ms = static_cast<uint32_t>(now_ms);
    event.type = mapped.type;
    event.value = mapped.value;
    event.command = command;
    event.data_length = static_cast<uint8_t>(length);
    event.stored_length = static_cast<uint8_t>(
        length > kMaxStoredData ? kMaxStoredData : length);
    if (mapped.has_value) event.flags |= kFlagHasValue;
    if (mapped.known) event.flags |= kFlagKnown;
    if (event.stored_length > 0) {
        std::memcpy(event.data, data, event.stored_length);
    }
    event.checksum = command;
    for (int i = 0; i < length; ++i) {
        event.checksum = static_cast<uint8_t>(event.checksum + data[i]);
    }
    if (length > kMaxStoredData) {
        // Keep the real original length/checksum and a prefix. A partial
        // payload MUST NOT be represented as a complete valid AA55 frame.
        event.flags |= kFlagDataTruncated;
        return event;
    }
    event.frame_length = static_cast<uint8_t>(length + 6);
    event.frame[0] = 0xAA;
    event.frame[1] = 0x55;
    event.frame[2] = 0;
    event.frame[3] = static_cast<uint8_t>(length + 1);
    event.frame[4] = command;
    if (length > 0) std::memcpy(event.frame + 5, event.data, length);
    event.frame[length + 5] = event.checksum;
    return event;
}

void BaseEventJournal::Commit(Event& event)
{
    event.sequence = ++state_.total_recorded;
    events_[next_index_] = event;
    next_index_ = static_cast<uint8_t>((next_index_ + 1) % kCapacity);
    if (state_.size < kCapacity) ++state_.size;
}

bool BaseEventJournal::Observe(uint8_t command, const uint8_t* data,
                              int length, uint64_t now_ms)
{
    if (length < 0 || length > 249 || (length > 0 && data == nullptr)) {
        return false;
    }
    const auto mapped = base_event_mapper::Map(command, data, length);
    if (mapped.ignored || mapped.type == base_event_mapper::EventType::LinkConnected) {
        return true;
    }
    Event event = Prepare(mapped, command, data, length, now_ms);
    if (!TryEnter()) return false;
    if (mapped.type == base_event_mapper::EventType::MechanicalStatus) {
        if (state_.mechanical_status_valid && state_.mechanical_status == mapped.value) {
            Exit();
            return true;
        }
        state_.mechanical_status = mapped.value;
        state_.mechanical_status_valid = true;
    }
    Commit(event);
    Exit();
    return true;
}

bool BaseEventJournal::ObserveHeartbeat(uint64_t heartbeat_ms, uint64_t now_ms)
{
    if (heartbeat_ms > now_ms) return false;
    const uint8_t data[]{0x00, 0x01};
    Event event = Prepare(base_event_mapper::Map(0x00, data, 2), 0x00, data, 2, now_ms);
    if (!TryEnter()) return false;
    if ((disconnect_valid_ && heartbeat_ms <= disconnected_through_ms_) ||
        (state_.heartbeat_valid && heartbeat_ms < state_.last_heartbeat_ms)) {
        Exit();
        return true;  // stale mirror notification
    }
    state_.heartbeat_valid = true;
    state_.last_heartbeat_ms = heartbeat_ms;
    if (!state_.online) {
        state_.online = true;
        Commit(event);
    }
    Exit();
    return true;
}

bool BaseEventJournal::NotifyDisconnected(uint64_t expired_heartbeat_ms, uint64_t now_ms)
{
    if (expired_heartbeat_ms > now_ms) return false;
    Event event{};  // synthetic fact: no raw data, frame, checksum or value
    event.timestamp_ms = static_cast<uint32_t>(now_ms);
    event.type = base_event_mapper::EventType::LinkDisconnected;
    event.flags = kFlagSynthetic | kFlagKnown;
    if (!TryEnter()) return false;
    if ((state_.heartbeat_valid && expired_heartbeat_ms < state_.last_heartbeat_ms) ||
        (disconnect_valid_ && expired_heartbeat_ms <= disconnected_through_ms_)) {
        Exit();
        return true;
    }
    disconnected_through_ms_ = expired_heartbeat_ms;
    disconnect_valid_ = true;
    state_.mechanical_status_valid = false;
    if (state_.online) {
        state_.online = false;
        Commit(event);
    }
    Exit();
    return true;
}

bool BaseEventJournal::TryReadState(State* output) const
{
    if (output == nullptr || !TryEnter()) return false;
    *output = state_;
    Exit();
    return true;
}

bool BaseEventJournal::TryCopy(Event* output, uint8_t index) const
{
    if (output == nullptr || !TryEnter()) return false;
    if (index >= state_.size) {
        Exit();
        return false;
    }
    const uint8_t first = state_.size == kCapacity ? next_index_ : 0;
    *output = events_[(first + index) % kCapacity];
    Exit();
    return true;
}

bool BaseEventJournal::TryReadRecent(RecentSnapshot* output) const
{
    if (output == nullptr || !TryEnter()) return false;

    RecentSnapshot snapshot{};
    snapshot.state = state_;
    snapshot.retained_count = state_.size;
    snapshot.returned_count = state_.size < kRecentCapacity
                                  ? state_.size
                                  : kRecentCapacity;
    const uint8_t oldest = state_.size == kCapacity ? next_index_ : 0;
    const uint8_t first = state_.size <= kRecentCapacity
                              ? oldest
                              : static_cast<uint8_t>(
                                    (oldest + state_.size - kRecentCapacity) % kCapacity);
    for (uint8_t i = 0; i < snapshot.returned_count; ++i) {
        snapshot.events[i] = events_[(first + i) % kCapacity];
    }
    *output = snapshot;
    Exit();
    return true;
}
