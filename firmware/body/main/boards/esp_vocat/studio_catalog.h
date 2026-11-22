#pragma once

#include "studio_device_link.h"
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <inttypes.h>

namespace deskbot_studio_catalog {
constexpr size_t kMaxEntries = 128, kEntryBytes = 384, kMaxIndexBytes = 16384;
constexpr size_t kPageSize = 2, kReplyBytes = 2048;

struct Entry {
    char key[44]{}, resource[32]{}, digest[65]{}, json[kEntryBytes]{};
    uint32_t bytes = 0;
};
struct Snapshot {
    char catalog[65]{}, package[65]{}, index[65]{};
    uint16_t count = 0;
    Entry entries[kMaxEntries]{};
};
struct Page {
    char catalog[65]{}, package[65]{}, index[65]{};
    uint16_t count = 0, cursor = 0;
    char rows[kPageSize][kEntryBytes]{};
};
static_assert(sizeof(Snapshot) <= 68 * 1024, "catalog lives in bounded PSRAM");
static_assert(sizeof(Page) <= 1024, "bounded metadata copy");

// Only the resource owner calls these callbacks. The USB reader sees Page.
struct Source {
    void* context;
    bool (*asset)(void*, const char*, const uint8_t*&, size_t&);
    bool (*parsed)(void*, const char*, const uint8_t*, size_t, unsigned, bool, bool);
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
    bool (*full_frame)(void*, const uint8_t*, size_t) = nullptr;
#endif
};
struct Digest {
    void* context;
    bool (*start)(void*);
    bool (*update)(void*, const void*, size_t);
    bool (*finish)(void*, char*);
};
bool Build(Snapshot&, const uint8_t* index, size_t size, const char* package,
           Source source, Digest hash);
using Provider = bool (*)(uint16_t, Page&);

inline bool Cursor(const char* operation, uint16_t& result) {
    if (std::strncmp(operation, "catalog.", 8)) return false;
    const char* p = operation + 8;
    if (!*p || (*p == '0' && p[1])) return false;
    unsigned value = 0;
    for (; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
        value = value * 10 + static_cast<unsigned>(*p - '0');
        if (value >= kMaxEntries) return false;
    }
    if (value % kPageSize) return false;
    result = static_cast<uint16_t>(value);
    return true;
}

inline bool Format(const Page& page, const char* request, const char* target,
                   const char* boot, const char* session, uint32_t sequence,
                   uint64_t now, char* output, size_t capacity) {
    if (!output || capacity < kReplyBytes || !page.count || page.count > kMaxEntries ||
        page.cursor >= page.count || page.cursor % kPageSize) return false;
    char time[21], next[8] = "null";
    deskbot_studio_link::Decimal64(now, time);
    if (page.cursor + kPageSize < page.count)
        std::snprintf(next, sizeof(next), "%u", static_cast<unsigned>(page.cursor + kPageSize));
    const bool pair = page.cursor + 1 < page.count;
    const int size = std::snprintf(output, capacity,
        "{\"schema\":\"deskbot.studio_catalog_page/v1\",\"message_type\":\"catalog_page\","
        "\"request_id\":\"%s\",\"target_id\":\"%s\",\"boot_id\":\"%s\",\"session_id\":\"%s\","
        "\"sequence\":%" PRIu32 ",\"at_ms\":%s,\"catalog_id\":\"%s\",\"package_sha256\":\"%s\","
        "\"index_sha256\":\"%s\",\"cursor\":%u,\"next_cursor\":%s,\"total\":%u,"
        "\"entries\":[%s%s%s],\"controls_available\":false}",
        request, target, boot, session, sequence, time, page.catalog, page.package, page.index,
        static_cast<unsigned>(page.cursor), next, static_cast<unsigned>(page.count),
        page.rows[0], pair ? "," : "", pair ? page.rows[1] : "");
    return size > 0 && static_cast<size_t>(size) < capacity && static_cast<size_t>(size) < kReplyBytes;
}
}  // namespace deskbot_studio_catalog
