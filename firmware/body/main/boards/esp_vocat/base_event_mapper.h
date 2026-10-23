#ifndef BASE_EVENT_MAPPER_H
#define BASE_EVENT_MAPPER_H

#include <cstdint>

namespace base_event_mapper {

enum class EventType : uint8_t {
    Unknown = 0,
    LinkConnected,
    LinkDisconnected,
    MechanicalStatus,
    ActionCompleted,
    MagneticSwitch,
    AccessoryAttached,
    AccessoryDetached,
    PairingEntered,
    PairingCancelled,
    CalibrationStarted,
    CalibrationPositionRecorded,
    CalibrationCompleted,
};

struct MappedEvent {
    EventType type = EventType::Unknown;
    uint16_t value = 0;
    bool has_value = false;
    bool known = false;
    bool ignored = false;
};

MappedEvent Map(uint8_t command, const uint8_t* data, int data_length);
const char* TypeName(EventType type);
const char* EventName(EventType type, uint16_t value);
const char* AccessoryName(uint16_t value);
const char* MechanicalStatusName(uint16_t value);

}  // namespace base_event_mapper

#endif  // BASE_EVENT_MAPPER_H
