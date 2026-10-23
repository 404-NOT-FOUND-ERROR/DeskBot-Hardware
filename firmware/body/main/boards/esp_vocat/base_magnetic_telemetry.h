#ifndef BASE_MAGNETIC_TELEMETRY_H
#define BASE_MAGNETIC_TELEMETRY_H

#include <cstdint>

// The C61 base sends perception payloads through the existing AA55 UART
// component.  The payload after command 0x06 is:
// sensor kind, X high/low, Y high/low, Z high/low.
enum class BaseMagneticSensorKind : uint8_t {
    Unknown = 0,
    Bmm150 = 1,
    Qmc6309 = 2,
    Ss49e = 3,
};

struct BaseMagneticSample {
    bool valid = false;
    BaseMagneticSensorKind sensor_kind = BaseMagneticSensorKind::Unknown;
    int16_t x = 0;
    int16_t y = 0;
    int16_t z = 0;
};

// Decode only the documented seven-byte 0x06 payload.  Keeping this helper
// independent of ESP-IDF makes the wire contract testable on the host.
inline bool DecodeBaseMagneticSample(const uint8_t* data, int data_len,
                                     BaseMagneticSample* output)
{
    if (data == nullptr || output == nullptr || data_len != 7) {
        return false;
    }

    const auto decode_i16 = [data](int offset) {
        const uint16_t bits =
            (static_cast<uint16_t>(data[offset]) << 8) |
            static_cast<uint16_t>(data[offset + 1]);
        return static_cast<int16_t>(bits);
    };

    const uint8_t sensor_kind = data[0];
    output->sensor_kind = sensor_kind <= 3
        ? static_cast<BaseMagneticSensorKind>(sensor_kind)
        : BaseMagneticSensorKind::Unknown;
    output->x = decode_i16(1);
    output->y = decode_i16(3);
    output->z = decode_i16(5);
    output->valid = true;
    return true;
}

inline const char* BaseMagneticSensorKindName(BaseMagneticSensorKind kind)
{
    switch (kind) {
    case BaseMagneticSensorKind::Bmm150: return "BMM150";
    case BaseMagneticSensorKind::Qmc6309: return "QMC6309";
    case BaseMagneticSensorKind::Ss49e: return "SS49E";
    case BaseMagneticSensorKind::Unknown: break;
    }
    return "UNKNOWN";
}

#endif  // BASE_MAGNETIC_TELEMETRY_H
