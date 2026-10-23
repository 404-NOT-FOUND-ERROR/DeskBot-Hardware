#include "base_event_mapper.h"

namespace {

uint16_t ReadValue(const uint8_t* data, int data_length)
{
    if (data == nullptr || data_length < 2) {
        return 0;
    }
    return static_cast<uint16_t>(static_cast<uint16_t>(data[0]) << 8) |
           static_cast<uint16_t>(data[1]);
}

}  // namespace

namespace base_event_mapper {

MappedEvent Map(uint8_t command, const uint8_t* data, int data_length)
{
    MappedEvent event;
    if (data_length < 0 || data_length > 249 ||
        (data_length > 0 && data == nullptr)) {
        return event;
    }
    event.value = ReadValue(data, data_length);
    event.has_value = data_length >= 2;

    if (command == 0x06) {
        if (data_length == 7) {
            event.known = true;
            event.ignored = true;
        }
        return event;
    }

    if (data_length != 2) {
        return event;
    }
    if (command == 0x00 && event.value == 0x0001) {
        event.type = EventType::LinkConnected;
        event.known = true;
        return event;
    }
    if (command == 0x02 && event.value == 0x0010) {
        event.type = EventType::ActionCompleted;
        event.known = true;
        return event;
    }
    if (command == 0x05 && event.value <= 0x0002) {
        event.type = EventType::MechanicalStatus;
        event.known = true;
        return event;
    }
    if (command != 0x03) {
        return event;
    }
    if (event.value >= 0x0001 && event.value <= 0x0007) {
        event.type = EventType::MagneticSwitch;
        event.known = true;
        return event;
    }
    if ((event.value >= 0x0008 && event.value <= 0x0009) ||
        (event.value >= 0x000C && event.value <= 0x000F) ||
        (event.value >= 0x0010 && event.value <= 0x0013)) {
        event.type = (event.value & 1u) == 0
                         ? EventType::AccessoryAttached
                         : EventType::AccessoryDetached;
        event.known = true;
        return event;
    }
    if (event.value == 0x000A) {
        event.type = EventType::PairingEntered;
        event.known = true;
        return event;
    }
    if (event.value == 0x000B) {
        event.type = EventType::PairingCancelled;
        event.known = true;
        return event;
    }
    if (event.value == 0x0081) {
        event.type = EventType::CalibrationStarted;
        event.known = true;
        return event;
    }
    if (event.value == 0x0082) {
        event.type = EventType::CalibrationPositionRecorded;
        event.known = true;
        return event;
    }
    if (event.value == 0x0083) {
        event.type = EventType::CalibrationCompleted;
        event.known = true;
        return event;
    }
    return event;
}

const char* TypeName(EventType type)
{
    switch (type) {
    case EventType::LinkConnected: return "base.link.connected";
    case EventType::LinkDisconnected: return "base.link.disconnected";
    case EventType::MechanicalStatus: return "base.mechanical.status";
    case EventType::ActionCompleted: return "base.action.completed";
    case EventType::MagneticSwitch: return "base.magnetic_switch";
    case EventType::AccessoryAttached: return "base.accessory.attached";
    case EventType::AccessoryDetached: return "base.accessory.detached";
    case EventType::PairingEntered: return "base.pairing.entered";
    case EventType::PairingCancelled: return "base.pairing.cancelled";
    case EventType::CalibrationStarted: return "base.calibration.started";
    case EventType::CalibrationPositionRecorded: return "base.calibration.position_recorded";
    case EventType::CalibrationCompleted: return "base.calibration.completed";
    case EventType::Unknown: return "base.uart.unknown_event";
    }
    return "base.uart.unknown_event";
}

const char* EventName(EventType type, uint16_t value)
{
    if (type == EventType::MagneticSwitch) {
        switch (value) {
        case 0x0001: return "base.magnetic_switch.slide_down";
        case 0x0002: return "base.magnetic_switch.slide_up";
        case 0x0003: return "base.magnetic_switch.removed_from_up";
        case 0x0004: return "base.magnetic_switch.removed_from_down";
        case 0x0005: return "base.magnetic_switch.placed_from_up";
        case 0x0006: return "base.magnetic_switch.placed_from_down";
        case 0x0007: return "base.magnetic_switch.single_click";
        default: return "base.uart.unknown_event";
        }
    }
    return TypeName(type);
}

const char* AccessoryName(uint16_t value)
{
    switch (value) {
    case 0x0008: case 0x0009: return "fish";
    case 0x000C: case 0x000D: return "ice_cream";
    case 0x000E: case 0x000F: return "donut";
    case 0x0010: case 0x0011: return "iphone_lean_front";
    case 0x0012: case 0x0013: return "iphone_under_base";
    default: return "unknown";
    }
}

const char* MechanicalStatusName(uint16_t value)
{
    switch (value) {
    case 0x0000: return "homing";
    case 0x0001: return "ready";
    case 0x0002: return "failed";
    default: return "unknown";
    }
}

}  // namespace base_event_mapper
