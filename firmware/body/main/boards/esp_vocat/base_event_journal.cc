#include "base_event_journal.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

BaseEventJournal::BaseEventJournal()
{
    mutex_ = xSemaphoreCreateMutex();
}

BaseEventJournal::~BaseEventJournal()
{
    if (mutex_ != nullptr) {
        vSemaphoreDelete(mutex_);
    }
}

void BaseEventJournal::Record(const char* type, uint8_t command,
                              const uint8_t* data, int data_length,
                              int64_t timestamp_ms, uint16_t value,
                              bool has_value, bool synthetic)
{
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(5)) != pdTRUE) {
        return;
    }

    Event& event = events_[next_];
    event = Event{};
    event.sequence = next_sequence_++;
    event.type = type != nullptr ? type : "base.uart.unknown_event";
    event.command = command;
    event.value = value;
    event.has_value = has_value;
    event.synthetic = synthetic;
    event.data_length = static_cast<uint16_t>(std::clamp(
        data_length, 0, static_cast<int>(kMaxDataBytes))
    );
    event.timestamp_ms = timestamp_ms;
    if (data != nullptr && event.data_length > 0) {
        std::memcpy(event.data.data(), data, event.data_length);
    }

    // A derived state transition, such as a heartbeat timeout, has no
    // received bytes. Keep raw fields empty instead of inventing a frame.
    if (event.synthetic) {
        next_ = (next_ + 1) % kCapacity;
        count_ = std::min(count_ + 1, kCapacity);
        xSemaphoreGive(mutex_);
        return;
    }

    const int frame_data_length = event.data_length;
    const int frame_length_field = 1 + frame_data_length;
    char hex[3] = {};
    auto append_byte = [&event, &hex](uint8_t byte) {
        std::snprintf(hex, sizeof(hex), "%02X", byte);
        event.frame_hex += hex;
    };
    event.frame_hex.reserve(static_cast<size_t>((frame_data_length + 6) * 2));
    append_byte(0xAA);
    append_byte(0x55);
    append_byte(static_cast<uint8_t>((frame_length_field >> 8) & 0xFF));
    append_byte(static_cast<uint8_t>(frame_length_field & 0xFF));
    append_byte(command);
    uint8_t checksum = command;
    for (int i = 0; i < frame_data_length; ++i) {
        append_byte(event.data[static_cast<size_t>(i)]);
        checksum = static_cast<uint8_t>(checksum + event.data[static_cast<size_t>(i)]);
    }
    append_byte(checksum);

    next_ = (next_ + 1) % kCapacity;
    count_ = std::min(count_ + 1, kCapacity);
    xSemaphoreGive(mutex_);
}

void BaseEventJournal::RecordSynthetic(const char* type, uint8_t command,
                                       int64_t timestamp_ms, uint16_t value,
                                       bool has_value)
{
    Record(type, command, nullptr, 0, timestamp_ms, value, has_value, true);
}

std::vector<BaseEventJournal::Event> BaseEventJournal::Snapshot() const
{
    std::vector<Event> result;
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(10)) != pdTRUE) {
        return result;
    }

    result.reserve(count_);
    const size_t first = count_ == kCapacity ? next_ : 0;
    for (size_t i = 0; i < count_; ++i) {
        result.push_back(events_[(first + i) % kCapacity]);
    }
    xSemaphoreGive(mutex_);
    return result;
}

uint32_t BaseEventJournal::TotalRecorded() const
{
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(10)) != pdTRUE) {
        return 0;
    }
    const uint32_t next = next_sequence_;
    xSemaphoreGive(mutex_);
    return next > 0 ? next - 1 : 0;
}
