#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <inttypes.h>

namespace deskbot_studio_link {
constexpr size_t kMaxFrameBytes = 2048;
constexpr uint32_t kHelloIntervalMs = 10000;

// Nano newlib supports 32-bit printf arguments. Render the one 64-bit field
// directly so the formatter only receives strings and 32-bit integers.
inline void Decimal64(uint64_t value, char (&output)[21]) {
    char reversed[20];
    size_t count = 0;
    do {
        reversed[count++] = static_cast<char>('0' + value % 10);
        value /= 10;
    } while (value);
    for (size_t i = 0; i < count; ++i) output[i] = reversed[count - i - 1];
    output[count] = '\0';
}

inline bool Hex(const char* value, size_t length) {
    if (!value || std::strlen(value) != length) return false;
    for (size_t i = 0; i < length; ++i) {
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f'))) return false;
    }
    return true;
}

// Shared by the ESP32 task and host tests. All strings have a bounded alphabet.
inline bool BuildHello(char* output, size_t capacity, const char* device_hex,
                       const char* boot_hex, const char* app_hash, const char* version,
                       uint32_t sequence, uint64_t uptime_ms,
                       uint32_t flash_bytes, uint32_t psram_bytes) {
    if (!output || !capacity) return false;
    output[0] = '\0';
    if (!Hex(device_hex, 32) || !Hex(boot_hex, 32) || !Hex(app_hash, 64) ||
        !version || !version[0] || std::strlen(version) > 31 ||
        uptime_ms > 9007199254740991ULL) return false;
    for (const char* p = version; *p; ++p) {
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
              (*p >= '0' && *p <= '9') || *p == '.' || *p == '_' || *p == '-')) return false;
    }
    char uptime_text[21];
    Decimal64(uptime_ms, uptime_text);
    const int length = std::snprintf(output, capacity,
        "{\"schema\":\"deskbot.device_link/v1\",\"message_type\":\"hello\","
        "\"device_id\":\"device-%s\",\"boot_id\":\"boot-%s\","
        "\"sequence\":%" PRIu32 ",\"uptime_ms\":%s,"
        "\"firmware\":{\"edition\":\"E3b\",\"version\":\"%s\",\"app_elf_sha256\":\"%s\"},"
        "\"hardware\":{\"chip\":\"esp32s3\",\"board\":\"esp_vocat\","
        "\"flash_bytes\":%" PRIu32 ",\"psram_bytes\":%" PRIu32 "},"
        "\"capabilities\":[\"device_identity\"],\"transport\":\"usb_console\","
        "\"controls_available\":false,\"catalog_id\":null,"
        "\"limits\":{\"max_frame_bytes\":2048,\"hello_interval_ms\":10000}}",
        device_hex, boot_hex, sequence, uptime_text, version, app_hash, flash_bytes, psram_bytes);
    if (length < 0 || static_cast<size_t>(length) >= capacity ||
        static_cast<size_t>(length) >= kMaxFrameBytes - 80) {
        output[0] = '\0';
        return false;
    }
    return true;
}

void Start();
}  // namespace deskbot_studio_link
