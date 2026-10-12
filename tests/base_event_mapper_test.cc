#include "base_event_mapper.h"

#include <cassert>
#include <cstdint>
#include <cstring>

namespace {

void expect_type(uint8_t command, uint16_t value, const char* expected)
{
    const uint8_t data[] = {
        static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value & 0xFF),
    };
    const auto mapped = base_event_mapper::Map(command, data, sizeof(data));
    assert(mapped.known);
    assert(!mapped.ignored);
    assert(mapped.has_value);
    assert(mapped.value == value);
    assert(std::strcmp(mapped.type, expected) == 0);
}

}  // namespace

int main()
{
    // Standard heartbeat, action, mechanical status, slide/accessory/pairing,
    // and calibration mappings.
    expect_type(0x00, 0x0001, "base.link.connected");
    expect_type(0x02, 0x0010, "base.action.completed");
    expect_type(0x05, 0x0001, "base.mechanical.status");
    expect_type(0x03, 0x0001, "base.magnetic_switch.slide_down");
    expect_type(0x03, 0x0007, "base.magnetic_switch.single_click");
    expect_type(0x03, 0x0008, "base.accessory.fish_attached");
    expect_type(0x03, 0x000F, "base.accessory.donut_detached");
    expect_type(0x03, 0x0081, "base.calibration.started");
    expect_type(0x03, 0x0083, "base.calibration.step2");

    // Perception samples are telemetry, never journal events.
    const uint8_t perception[] = {1, 0, 190, 0xFD, 0xDC, 0xFC, 0x54};
    const auto sample = base_event_mapper::Map(0x06, perception, sizeof(perception));
    assert(sample.known);
    assert(sample.ignored);

    // Malformed and unknown frames remain visible as unknown events.
    const uint8_t short_data[] = {0x00};
    assert(!base_event_mapper::Map(0x03, short_data, sizeof(short_data)).known);
    assert(!base_event_mapper::Map(0x03, perception, sizeof(perception)).known);
    assert(!base_event_mapper::Map(0x7F, short_data, sizeof(short_data)).known);
    const uint8_t unknown_value[] = {0x12, 0x34};
    assert(!base_event_mapper::Map(0x03, unknown_value, sizeof(unknown_value)).known);

    assert(std::strcmp(base_event_mapper::MechanicalStatusName(0x0000), "homing") == 0);
    assert(std::strcmp(base_event_mapper::MechanicalStatusName(0x0001), "ready") == 0);
    assert(std::strcmp(base_event_mapper::MechanicalStatusName(0x0002), "failed") == 0);
    assert(std::strcmp(base_event_mapper::MechanicalStatusName(0xFFFF), "unknown") == 0);
    return 0;
}
