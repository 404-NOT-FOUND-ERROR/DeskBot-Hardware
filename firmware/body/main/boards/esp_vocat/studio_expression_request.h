#pragma once

#include "studio_device_link.h"
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <inttypes.h>

namespace deskbot_studio_expression {
constexpr char kSchema[] = "deskbot.expression_control/v1";
constexpr char kCatalog[] = "42a6d3c6b062ab63daf23a95128382909b63b06aa4d2416cb1da9464411b4fb5";
constexpr char kRole[] = "role-default";
constexpr uint64_t kMaxCounter = 9007199254740991ULL;

struct SessionView {
    const char* target = "";
    const char* boot = "";
    const char* session = "";
    uint32_t epoch = 0;
    uint64_t lease_deadline = 0;
    bool active = false;
};

enum class Kind : uint8_t { Expression, Action };
struct Request {
    char request_id[97]{}, target_id[97]{}, boot_id[97]{}, session_id[97]{};
    char catalog_id[65]{}, role_id[97]{}, issued_at[25]{}, command_id[97]{};
    uint64_t sequence = 0;
    uint32_t ttl_ms = 0;
    Kind kind = Kind::Expression;
};

inline bool Identifier(const char* value) {
    const auto alpha = [](char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); };
    if (!alpha(*value)) return false;
    for (const char* p = value + 1; *p; ++p)
        if (!alpha(*p) && !(*p >= '0' && *p <= '9') && *p != '_' && *p != '-' && *p != '.' && *p != ':') return false;
    return true;
}

// Fixed schema reader, sharing the existing USB line framer. No heap or cJSON
// hooks. UTC is metadata; the monotonic deadline starts at complete-frame input.
class Decoder {
public:
    Decoder(const uint8_t* data, size_t size) : data_(data), size_(size) {}
    bool Read(Request& request) {
        if (!data_ || !size_ || size_ > 2048 || !Take('{')) return false;
        unsigned seen = 0;
        for (unsigned field = 0; field < 12; ++field) {
            char key[32], value[97];
            if ((field && !Take(',')) || !String(key, sizeof(key)) || !Take(':')) return false;
            const char* names[] = {"schema", "message_type", "request_id", "target_id", "boot_id", "session_id",
                                   "sequence", "catalog_id", "role_id", "issued_at", "expires_after_ms", "command"};
            unsigned index = 0;
            while (index < 12 && std::strcmp(key, names[index])) ++index;
            if (index == 12 || (seen & (1U << index))) return false;
            seen |= 1U << index;
            if (index == 6 || index == 10) {
                uint64_t number = 0;
                if (!Number(number) || !number) return false;
                if (index == 6) request.sequence = number;
                else {
                    if (number < 100 || number > 10000) return false;
                    request.ttl_ms = static_cast<uint32_t>(number);
                }
            } else if (index == 11) {
                if (!Command(request)) return false;
            } else {
                if (!String(value, sizeof(value))) return false;
                if (index == 0) { if (std::strcmp(value, kSchema)) return false; }
                else if (index == 1) { if (std::strcmp(value, "request")) return false; }
                else if (index == 7) {
                    if (!deskbot_studio_link::Hex(value, 64)) return false;
                    std::memcpy(request.catalog_id, value, 65);
                } else if (index == 9) {
                    if (!Timestamp(value)) return false;
                    std::memcpy(request.issued_at, value, 25);
                } else {
                    if (!Identifier(value)) return false;
                    char* fields[] = {nullptr, nullptr, request.request_id, request.target_id, request.boot_id,
                                       request.session_id, nullptr, nullptr, request.role_id};
                    std::memcpy(fields[index], value, std::strlen(value) + 1);
                }
            }
        }
        if (!Take('}')) return false;
        Space();
        return seen == 4095 && offset_ == size_;
    }
private:
    void Space() {
        while (offset_ < size_ && (data_[offset_] == ' ' || data_[offset_] == '\t' ||
               data_[offset_] == '\r' || data_[offset_] == '\n')) ++offset_;
    }
    bool Take(uint8_t byte) {
        Space();
        if (offset_ == size_ || data_[offset_] != byte) return false;
        ++offset_; return true;
    }
    bool Number(uint64_t& value) {
        Space();
        const size_t start = offset_;
        if (offset_ == size_ || data_[offset_] < '0' || data_[offset_] > '9') return false;
        do {
            const unsigned digit = data_[offset_] - '0';
            if (value > (kMaxCounter - digit) / 10) return false;
            value = value * 10 + digit;
            ++offset_;
        } while (offset_ < size_ && data_[offset_] >= '0' && data_[offset_] <= '9');
        return offset_ - start == 1 || data_[start] != '0';
    }
    static int Hex(uint8_t c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }
    bool String(char* output, size_t capacity) {
        if (!Take('"')) return false;
        size_t count = 0;
        while (offset_ < size_) {
            unsigned c = data_[offset_++];
            if (c == '"') { output[count] = 0; return true; }
            if (c == '\\') {
                if (offset_ == size_) return false;
                c = data_[offset_++];
                if (c == 'u') {
                    c = 0;
                    for (unsigned i = 0; i < 4; ++i) {
                        if (offset_ == size_) return false;
                        const int digit = Hex(data_[offset_++]);
                        if (digit < 0) return false;
                        c = c * 16 + static_cast<unsigned>(digit);
                    }
                } else if (c != '"' && c != '\\' && c != '/') return false;
            }
            if (c < 0x20 || c > 0x7e || count + 1 >= capacity) return false;
            output[count++] = static_cast<char>(c);
        }
        return false;
    }
    static bool Timestamp(const char* text) {
        if (std::strlen(text) != 24) return false;
        for (unsigned i = 0; i < 24; ++i) {
            char literal = 0;
            if (i == 4 || i == 7) literal = '-';
            else if (i == 10) literal = 'T';
            else if (i == 13 || i == 16) literal = ':';
            else if (i == 19) literal = '.';
            else if (i == 23) literal = 'Z';
            if (literal ? text[i] != literal : text[i] < '0' || text[i] > '9') return false;
        }
        const auto pair = [text](unsigned i) { return (text[i]-'0')*10 + text[i+1]-'0'; };
        const unsigned year = pair(0)*100 + pair(2), month = pair(5), day = pair(8);
        const unsigned days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
        if (!year || month < 1 || month > 12) return false;
        const bool leap = !(year % 4) && ((year % 100) || !(year % 400));
        return day >= 1 && day <= days[month-1] + (month == 2 && leap ? 1U : 0U) &&
               pair(11) < 24 && pair(14) < 60 && pair(17) < 60;
    }
    bool Command(Request& request) {
        if (!Take('{')) return false;
        char kind[97]{}, id[97]{};
        unsigned seen = 0;
        bool action = false;
        for (unsigned i = 0; i < 2; ++i) {
            char key[32], value[97];
            if ((i && !Take(',')) || !String(key, sizeof(key)) || !Take(':') || !String(value, sizeof(value))) return false;
            if (!std::strcmp(key, "kind")) {
                if (seen & 1) return false;
                seen |= 1; std::memcpy(kind, value, std::strlen(value)+1);
            } else if (!std::strcmp(key, "semantic_id") || !std::strcmp(key, "action_id")) {
                if ((seen & 2) || !Identifier(value)) return false;
                seen |= 2; action = !std::strcmp(key, "action_id"); std::memcpy(id, value, std::strlen(value)+1);
            } else return false;
        }
        if (!Take('}') || seen != 3 || std::strcmp(kind, action ? "play_action" : "play_expression")) return false;
        request.kind = action ? Kind::Action : Kind::Expression;
        std::memcpy(request.command_id, id, sizeof(id));
        return true;
    }
    const uint8_t* data_;
    size_t size_, offset_ = 0;
};

using Submit = bool (*)(const uint8_t*, size_t, const SessionView&, uint64_t, char*, size_t);
using Synchronize = void (*)(const SessionView&, uint64_t);
using Disconnect = void (*)();
struct Hooks { Submit submit = nullptr; Synchronize sync = nullptr; Disconnect disconnect = nullptr; };
static_assert(sizeof(Request) <= 800, "bounded expression decoder request");
}  // namespace deskbot_studio_expression
