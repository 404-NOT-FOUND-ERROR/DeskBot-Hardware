#ifndef DESKBOT_BASE_EVENT_MAPPER_H
#define DESKBOT_BASE_EVENT_MAPPER_H

#include <cstdint>

namespace base_event_mapper {

// A transport-neutral description of a validated base frame. The mapper does
// not update state and never triggers hardware side effects.
struct MappedEvent {
    const char* type = "base.uart.unknown_event";
    uint16_t value = 0;
    bool has_value = false;
    bool known = false;
    bool ignored = false;
    bool mechanical_status = false;
};

MappedEvent Map(uint8_t command, const uint8_t* data, int data_length);
const char* MechanicalStatusName(uint16_t status);

}  // namespace base_event_mapper

#endif  // DESKBOT_BASE_EVENT_MAPPER_H
