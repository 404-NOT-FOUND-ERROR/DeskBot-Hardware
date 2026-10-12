#include "base_event_mapper.h"

namespace {

constexpr uint8_t kHeartbeat = 0x00;
constexpr uint8_t kAction = 0x02;
constexpr uint8_t kMagneticSwitch = 0x03;
constexpr uint8_t kMechanicalStatus = 0x05;
constexpr uint8_t kPerception = 0x06;

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
    event.value = ReadValue(data, data_length);
    event.has_value = data != nullptr && data_length >= 2;

    if (command == kPerception) {
        // A valid sample is sensor kind plus three big-endian axes. It belongs
        // to telemetry, not the event list. Malformed samples remain visible.
        if (data != nullptr && data_length == 7) {
            event.known = true;
            event.ignored = true;
        }
        return event;
    }

    // All currently documented event/status commands carry one uint16 value.
    // The UART component already validates the outer frame and checksum; this
    // layer validates the command payload shape and semantic value.
    if (data == nullptr || data_length != 2) {
        return event;
    }

    if (command == kHeartbeat) {
        if (event.value == 0x0001) {
            event.type = "base.link.connected";
            event.known = true;
        }
        return event;
    }

    if (command == kMechanicalStatus) {
        if (event.value <= 0x0002) {
            event.type = "base.mechanical.status";
            event.known = true;
            event.mechanical_status = true;
        }
        return event;
    }

    if (command == kAction) {
        if (event.value == 0x0010) {
            event.type = "base.action.completed";
            event.known = true;
        }
        return event;
    }

    if (command != kMagneticSwitch) {
        return event;
    }

    switch (event.value) {
    case 0x0000: event.type = "base.magnetic_switch.initial_state"; break;
    case 0x0001: event.type = "base.magnetic_switch.slide_down"; break;
    case 0x0002: event.type = "base.magnetic_switch.slide_up"; break;
    case 0x0003: event.type = "base.magnetic_switch.removed_from_up"; break;
    case 0x0004: event.type = "base.magnetic_switch.removed_from_down"; break;
    case 0x0005: event.type = "base.magnetic_switch.placed_from_up"; break;
    case 0x0006: event.type = "base.magnetic_switch.placed_from_down"; break;
    case 0x0007: event.type = "base.magnetic_switch.single_click"; break;
    case 0x0008: event.type = "base.accessory.fish_attached"; break;
    case 0x0009: event.type = "base.accessory.fish_detached"; break;
    case 0x000A: event.type = "base.pairing.detected"; break;
    case 0x000B: event.type = "base.pairing.cancelled"; break;
    case 0x000C: event.type = "base.accessory.ice_cream_attached"; break;
    case 0x000D: event.type = "base.accessory.ice_cream_detached"; break;
    case 0x000E: event.type = "base.accessory.donut_attached"; break;
    case 0x000F: event.type = "base.accessory.donut_detached"; break;
    case 0x0010: event.type = "base.accessory.iphone_lean_front"; break;
    case 0x0011: event.type = "base.accessory.iphone_lean_front_detached"; break;
    case 0x0012: event.type = "base.accessory.iphone_under_base"; break;
    case 0x0013: event.type = "base.accessory.iphone_under_base_detached"; break;
    case 0x0081: event.type = "base.calibration.started"; break;
    case 0x0082: event.type = "base.calibration.step1"; break;
    case 0x0083: event.type = "base.calibration.step2"; break;
    default: return event;
    }

    event.known = true;
    return event;
}

const char* MechanicalStatusName(uint16_t status)
{
    switch (status) {
    case 0x0000: return "homing";
    case 0x0001: return "ready";
    case 0x0002: return "failed";
    default: return "unknown";
    }
}

}  // namespace base_event_mapper
