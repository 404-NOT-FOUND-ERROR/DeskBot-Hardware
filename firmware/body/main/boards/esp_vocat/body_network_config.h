#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <inttypes.h>

namespace deskbot_body_network {
struct Config {
    char host[64]{};
    uint32_t revision = 0;
    uint16_t touch_port = 0, expression_port = 0;
    uint8_t saved = 0;
};
inline bool Number(const char* text, uint32_t& value) {
    value = 0;
    if (!text || !*text || (*text == '0' && text[1])) return false;
    for (; *text; ++text) {
        if (*text < '0' || *text > '9' || value > (UINT32_MAX - (*text - '0')) / 10) return false;
        value = value * 10 + (*text - '0');
    }
    return true;
}
inline bool Host(const char* text) {
    if (!text || !*text || std::strlen(text) > 63) return false;
    bool numeric = true;
    for (const char* p = text; *p; ++p) if ((*p < '0' || *p > '9') && *p != '.') numeric = false;
    if (numeric) {
        unsigned octets[4]{};
        const char* p = text;
        for (unsigned i = 0; i < 4; ++i) {
            const char* first = p;
            while (*p >= '0' && *p <= '9') {
                octets[i] = octets[i] * 10 + (*p++ - '0');
                if (octets[i] > 255) return false;
            }
            if (p == first || (p - first > 1 && *first == '0') || (i < 3 ? *p++ != '.' : *p != 0)) return false;
        }
        return octets[0] == 10 || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
            || (octets[0] == 192 && octets[1] == 168);
    }
    size_t label = 0;
    char previous = 0;
    for (const char* p = text; ; ++p) {
        if (*p == '.' || !*p) {
            if (!label || previous == '-') return false;
            if (!*p) return true;
            label = 0;
        } else {
            if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || (*p == '-' && label))) return false;
            if (++label > 63) return false;
        }
        previous = *p;
    }
}
inline bool Valid(const Config& c) { return c.saved <= 1 && Host(c.host) && c.touch_port && c.expression_port && c.touch_port != c.expression_port; }
inline bool Same(const Config& a, const Config& b) {
    return !std::strcmp(a.host, b.host) && a.touch_port == b.touch_port
        && a.expression_port == b.expression_port && a.saved == b.saved;
}
enum class Kind { Read, Save, Reset };
struct Command { Kind kind = Kind::Read; uint32_t expected = 0; Config config{}; };
inline bool Operation(const char* op) {
    return !std::strcmp(op, "net-get") || !std::strncmp(op, "net-set:", 8) || !std::strncmp(op, "net-reset:", 10);
}
inline bool Decode(const char* op, Command& out) {
    if (!std::strcmp(op, "net-get")) { out = {}; return true; }
    char text[97]{};
    if (std::strlen(op) >= sizeof(text)) return false;
    std::strcpy(text, op);
    char* parts[5]{text}; unsigned count = 1;
    for (char* p = text; *p; ++p) if (*p == ':') {
        if (count == 5) return false;
        *p = 0; parts[count++] = p + 1;
    }
    Command c{};
    if (count == 2 && !std::strcmp(parts[0], "net-reset") && Number(parts[1], c.expected)) c.kind = Kind::Reset;
    else if (count == 5 && !std::strcmp(parts[0], "net-set") && Number(parts[1], c.expected) && Host(parts[2])) {
        uint32_t touch = 0, expression = 0;
        if (!Number(parts[3], touch) || !Number(parts[4], expression) || !touch || !expression
            || touch > UINT16_MAX || expression > UINT16_MAX || touch == expression) return false;
        c.kind = Kind::Save; std::strcpy(c.config.host, parts[2]);
        c.config.touch_port = static_cast<uint16_t>(touch); c.config.expression_port = static_cast<uint16_t>(expression);
        c.config.saved = true;
    } else return false;
    out = c; return true;
}
struct Reply {
    Config config{};
    uint32_t applied_revision = 0;
    bool applied = false;
    const char* result = "unavailable";
    const char* storage = "defaults";
};
using Provider = bool (*)(const char*, Reply&);
inline bool Format(const Reply& row, const char* request, const char* target, const char* boot,
                   const char* session, uint32_t sequence, const char* now, char* output, size_t capacity) {
    char applied[16] = "null";
    if (row.applied) std::snprintf(applied, sizeof(applied), "%" PRIu32, row.applied_revision);
    const int n = std::snprintf(output, capacity,
        "{\"schema\":\"deskbot.body_network/v1\",\"message_type\":\"network\",\"request_id\":\"%s\","
        "\"target_id\":\"%s\",\"boot_id\":\"%s\",\"session_id\":\"%s\",\"sequence\":%" PRIu32 ","
        "\"at_ms\":%s,\"lease_ms\":30000,\"result\":\"%s\",\"storage\":\"%s\","
        "\"host\":\"%s\",\"touch_port\":%u,\"expression_port\":%u,\"revision\":%" PRIu32 ","
        "\"source\":\"%s\",\"applied_revision\":%s}",
        request, target, boot, session, sequence, now, row.result, row.storage, row.config.host,
        static_cast<unsigned>(row.config.touch_port), static_cast<unsigned>(row.config.expression_port),
        row.config.revision, row.config.saved ? "saved" : "defaults", applied);
    return n > 0 && static_cast<size_t>(n) < capacity;
}
bool Initialize(const char* host, uint16_t touch, uint16_t expression);
bool Handle(const char* operation, Reply& reply);
bool ApplyUrls();  // Called only between HTTP requests by the shared network task.
uint32_t DesiredRevision();
uint32_t AppliedRevision();  // Read by the worker after applying URLs.
const char* TouchUrl();
const char* ExpressionUrl();
}  // namespace deskbot_body_network
