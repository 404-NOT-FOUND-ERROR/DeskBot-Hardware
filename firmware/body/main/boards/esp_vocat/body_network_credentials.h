#pragma once
#include "body_network_config.h"
#include <memory>

namespace deskbot_body_credentials {
constexpr size_t kMaxCertificate = 1536;
constexpr size_t kHeaderBytes = 88;
constexpr size_t kMaxBytes = kHeaderBytes + 2 * kMaxCertificate;
constexpr uint64_t kStageLeaseMs = 30000;

enum class Channel { Touch = 0, Expression = 1 };
struct Profile {
    uint8_t bytes[kMaxBytes]{};
    size_t size = 0;
    char id[33]{};
    char authorization[2][72]{};
    uint16_t cert_size[2]{};
    bool Decode();
    const char* Certificate(Channel channel) const {
        return reinterpret_cast<const char*>(bytes + kHeaderBytes
            + (channel == Channel::Expression ? cert_size[0] : 0));
    }
};
using Snapshot = std::shared_ptr<const Profile>;
struct Trial {
    Snapshot profile;
    uint32_t address_revision = 0;
    uint64_t ticket = 0;  // A live check can use the built-in trust pair.
};
struct Reply {
    char active[33]{};
    char candidate[33]{};
    const char* result = "invalid";
    const char* storage = "builtin";
    const char* trial = "none";
};
using Provider = bool (*)(const char*, const char*, uint64_t, Reply&);
inline bool Operation(const char* operation) {
    return !std::strcmp(operation, "key-get") || !std::strcmp(operation, "key-test")
        || !std::strcmp(operation, "key-check")
        || !std::strncmp(operation, "key-begin:", 10) || !std::strncmp(operation, "key-data:", 9)
        || !std::strncmp(operation, "key-save:", 9);
}
inline bool Format(const Reply& row, const char* request, const char* target, const char* boot,
                   const char* session, uint32_t sequence, const char* now, char* output, size_t capacity) {
    const int size = std::snprintf(output, capacity,
        "{\"schema\":\"deskbot.body_credentials/v1\",\"message_type\":\"credentials\",\"request_id\":\"%s\","
        "\"target_id\":\"%s\",\"boot_id\":\"%s\",\"session_id\":\"%s\",\"sequence\":%" PRIu32 ","
        "\"at_ms\":%s,\"lease_ms\":30000,\"result\":\"%s\",\"storage\":\"%s\","
        "\"active\":\"%s\",\"candidate\":\"%s\",\"trial\":\"%s\"}",
        request, target, boot, session, sequence, now, row.result, row.storage, row.active, row.candidate, row.trial);
    return size > 0 && static_cast<size_t>(size) < capacity;
}
bool Initialize(const char* builtin_id);
bool Handle(const char* operation, const char* session, uint64_t now, Reply& reply);
Snapshot Active();
Trial TakeTrial(uint64_t now, uint32_t applied_address);
void FinishTrial(const Trial& trial, bool touch, bool expression);
}  // namespace deskbot_body_credentials
