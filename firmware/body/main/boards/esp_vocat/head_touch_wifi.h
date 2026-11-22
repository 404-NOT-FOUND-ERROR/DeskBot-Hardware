#pragma once

#include "studio_device_link.h"
#include <cstdint>
#include <cstdio>

namespace deskbot_head_touch_wifi {

constexpr unsigned kQueueLength = 8;
constexpr uint64_t kMaxAgeMs = 300000;

inline bool Fresh(uint64_t event_ms, uint64_t now_ms) {
    return now_ms >= event_ms && now_ms - event_ms < kMaxAgeMs;
}

inline uint32_t RetryDelayMs(unsigned attempt) {
    constexpr uint32_t delays[] = {1000, 2000, 4000, 8000, 16000, 30000};
    return delays[attempt < 5 ? attempt : 5];
}

enum class Delivery { Retry, Recorded, Rejected };

inline Delivery Classify(int status) {
    if (status == 204) return Delivery::Recorded;
    if (status == 400 || status == 401 || status == 404 || status == 409) return Delivery::Rejected;
    return Delivery::Retry;
}

inline int Encode(char* out, size_t capacity, const char* device, const char* boot,
                  const char* firmware, uint64_t event_ms, uint64_t reported_ms) {
    using namespace deskbot_studio_link;
    if (!Hex(device, 32) || !Hex(boot, 32) || !Hex(firmware, 64) ||
        !Fresh(event_ms, reported_ms) || reported_ms > 9007199254740991ULL) return 0;
    char event_text[21], reported_text[21];
    Decimal64(event_ms, event_text);
    Decimal64(reported_ms, reported_text);
    const int size = std::snprintf(out, capacity,
        "{\"device_id\":\"device-%s\",\"boot_id\":\"boot-%s\",\"firmware_sha256\":\"%s\","
        "\"uptime_ms\":%s,\"reported_uptime_ms\":%s}",
        device, boot, firmware, event_text, reported_text);
    return size > 0 && static_cast<size_t>(size) < capacity ? size : 0;
}

#if __has_include("head_touch_wifi.local.h")
bool Prepare(const char* device, const char* boot, const char* firmware);
uint32_t Poll();
void Disable();  // Startup failure only; no running worker may own the queue.
void Start(const char* device, const char* boot, const char* firmware);
void Observe(uint64_t event_ms);
#else
inline void Start(const char*, const char*, const char*) {}
inline void Observe(uint64_t) {}
#endif

}  // namespace deskbot_head_touch_wifi
