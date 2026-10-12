#ifndef BASE_EVENT_JOURNAL_H
#define BASE_EVENT_JOURNAL_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

// A small, device-local audit trail for validated base UART frames.
// It deliberately records facts only; it does not trigger actions or alter
// the existing BaseControl dispatch path.
class BaseEventJournal {
public:
    static constexpr size_t kCapacity = 24;
    // The official parser accepts a length field of at most 250 bytes. One
    // byte is the command, so the callback can expose at most 249 data bytes.
    static constexpr size_t kMaxDataBytes = 249;

    struct Event {
        uint32_t sequence = 0;
        std::string type;
        uint8_t command = 0;
        uint16_t value = 0;
        bool has_value = false;
        uint16_t data_length = 0;
        std::array<uint8_t, kMaxDataBytes> data{};
        int64_t timestamp_ms = 0;
        bool synthetic = false;
        std::string frame_hex;
    };

    BaseEventJournal();
    ~BaseEventJournal();

    BaseEventJournal(const BaseEventJournal&) = delete;
    BaseEventJournal& operator=(const BaseEventJournal&) = delete;

    // Record one already checksum-validated frame. Heartbeat/perception
    // filtering is owned by BaseControl so this class stays transport-neutral.
    void Record(const char* type, uint8_t command, const uint8_t* data,
                int data_length, int64_t timestamp_ms, uint16_t value = 0,
                bool has_value = false, bool synthetic = false);
    void RecordSynthetic(const char* type, uint8_t command, int64_t timestamp_ms,
                         uint16_t value = 0, bool has_value = false);

    std::vector<Event> Snapshot() const;
    uint32_t TotalRecorded() const;

private:
    mutable SemaphoreHandle_t mutex_ = nullptr;
    std::array<Event, kCapacity> events_{};
    size_t count_ = 0;
    size_t next_ = 0;
    uint32_t next_sequence_ = 1;
};

#endif // BASE_EVENT_JOURNAL_H
