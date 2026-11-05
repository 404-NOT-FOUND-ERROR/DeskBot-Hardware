#pragma once

#include "studio_request_framer.h"
#include "studio_device_link.h"
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <inttypes.h>
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
#include "studio_expression_request.h"
#endif
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
#include "studio_runtime_query.h"
#endif
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
#include "studio_display_readiness.h"
#endif

namespace deskbot_studio_session {
constexpr char kSchema[] = "deskbot.studio_session/v1";
constexpr uint32_t kLeaseMs = 30000;
constexpr uint32_t kReplyIntervalMs = 250;
constexpr uint32_t kPartialTimeoutMs = 2000;
constexpr uint64_t kMaxClock = 9007199254740991ULL;

struct Request {
    char operation[97]{};
    char request_id[97]{};
    char target_id[97]{};
    char boot_id[97]{};
    char client_nonce[97]{};
    char session_id[97]{};
    bool session_null = false;
};

inline bool Identifier(const char* value) {
    const auto alpha = [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
    };
    if (!alpha(value[0])) return false;
    for (const char* p = value + 1; *p; ++p) {
        if (!alpha(*p) && !(*p >= '0' && *p <= '9') &&
            *p != '_' && *p != '-' && *p != '.' && *p != ':') return false;
    }
    return true;
}

// Schema-specific flat JSON reader. Seven bounded ASCII strings (session may
// be null); standard JSON escapes are decoded before field/ID validation.
// No heap, recursive parser, global cJSON hooks or application state.
class Decoder {
public:
    Decoder(const uint8_t* data, size_t size) : data_(data), size_(size) {}
    bool Read(Request& request) {
        if (!data_ || size_ == 0 || size_ > 2048 || !Take('{')) return false;
        unsigned seen = 0;
        for (unsigned field = 0; field < 7; ++field) {
            char key[32], value[97];
            if ((field && !Take(',')) || !String(key, sizeof(key)) || !Take(':')) return false;
            const char* names[] = {"schema", "operation", "request_id", "target_id",
                                   "boot_id", "client_nonce", "session_id"};
            unsigned index = 0;
            while (index < 7 && std::strcmp(key, names[index])) ++index;
            if (index == 7 || (seen & (1U << index))) return false;
            seen |= 1U << index;
            if (index == 6 && Null()) {
                request.session_null = true;
                continue;
            }
            if (!String(value, sizeof(value))) return false;
            if (index == 0) {
                if (std::strcmp(value, kSchema)) return false;
            } else {
                if (!Identifier(value)) return false;
                char* fields[] = {nullptr, request.operation, request.request_id,
                                  request.target_id, request.boot_id, request.client_nonce, request.session_id};
                std::memcpy(fields[index], value, std::strlen(value) + 1);
            }
        }
        if (!Take('}')) return false;
        Space();
        return offset_ == size_ && seen == 127;
    }
private:
    void Space() {
        while (offset_ < size_ && (data_[offset_] == ' ' || data_[offset_] == '\t' ||
               data_[offset_] == '\n' || data_[offset_] == '\r')) ++offset_;
    }
    bool Take(uint8_t byte) {
        Space();
        if (offset_ == size_ || data_[offset_] != byte) return false;
        ++offset_;
        return true;
    }
    bool Null() {
        Space();
        if (size_ - offset_ < 4 || std::memcmp(data_ + offset_, "null", 4)) return false;
        offset_ += 4;
        return true;
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
            if (c == '"') {
                output[count] = 0;
                return true;
            }
            if (c == '\\') {
                if (offset_ == size_) return false;
                c = data_[offset_++];
                switch (c) {
                    case '"': case '\\': case '/': break;
                    case 'b': c = '\b'; break;
                    case 'f': c = '\f'; break;
                    case 'n': c = '\n'; break;
                    case 'r': c = '\r'; break;
                    case 't': c = '\t'; break;
                    case 'u':
                        c = 0;
                        for (int i = 0; i < 4; ++i) {
                            if (offset_ == size_) return false;
                            const int nibble = Hex(data_[offset_++]);
                            if (nibble < 0) return false;
                            c = c * 16 + static_cast<unsigned>(nibble);
                        }
                        break;
                    default: return false;
                }
            }
            if (c < 0x20 || c > 0x7e || count + 1 >= capacity) return false;
            output[count++] = static_cast<char>(c);
        }
        return false;
    }
    const uint8_t* data_;
    size_t size_, offset_ = 0;
};

class Session {
public:
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
    void SetDisplayProvider(deskbot_studio_display::Provider provider) { display_provider_ = provider; }
#endif
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
    void SetStateProvider(deskbot_studio_runtime_query::Provider provider) { provider_ = provider; }
#endif
    bool Initialize(const char* device_hex, const char* boot_hex) {
        if (!deskbot_studio_link::Hex(device_hex, 32) || !deskbot_studio_link::Hex(boot_hex, 32)) return false;
        std::snprintf(target_, sizeof(target_), "device-%s", device_hex);
        std::snprintf(boot_, sizeof(boot_), "boot-%s", boot_hex);
        return true;
    }
    void Disconnect() { active_ = false; expired_ = false; }
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
    deskbot_studio_expression::SessionView ViewAt(uint64_t now) const {
        return {target_, boot_, session_, epoch_, last_activity_ + kLeaseMs,
                active_ && now <= kMaxClock && now >= last_activity_ && now - last_activity_ < kLeaseMs};
    }
#endif
    bool Handle(const Request& request, uint64_t now, char* output, size_t capacity) {
        // Reserve output space before a state transition.
        if (!output || capacity < 1024 || !target_[0] || now > kMaxClock ||
            now < last_time_ || sequence_ == UINT32_MAX) return false;
        output[0] = 0;
        last_time_ = now;
        if (active_ && now - last_activity_ >= kLeaseMs) {
            active_ = false; expired_ = true;
        }
        const char* state = "rejected";
        const char* reason = "unsupported_operation";
        if (std::strcmp(request.target_id, target_)) reason = "target_mismatch";
        else if (std::strcmp(request.boot_id, boot_)) reason = "boot_changed";
        else if (!std::strcmp(request.operation, "open")) {
            if (!request.session_null) reason = "open_requires_null_session";
            else if (active_ && !std::strcmp(nonce_, request.client_nonce)) {
                last_activity_ = now;
                state = "ready"; reason = "session_resumed";
            } else if (epoch_ == UINT32_MAX) reason = "session_counter_exhausted";
            else {
                ++epoch_;
                std::snprintf(session_, sizeof(session_), "session-%s-%" PRIu32, boot_ + 5, epoch_);
                std::memcpy(nonce_, request.client_nonce, std::strlen(request.client_nonce) + 1);
                last_activity_ = now;
                active_ = true; expired_ = false;
                state = "ready"; reason = "session_opened";
            }
        } else if (!std::strcmp(request.operation, "ping") || !std::strcmp(request.operation, "close")
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
                   || !std::strcmp(request.operation, "state")
#endif
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
                   || !std::strcmp(request.operation, "display")
#endif
        ) {
            if (request.session_null || std::strcmp(nonce_, request.client_nonce) ||
                std::strcmp(session_, request.session_id)) reason = "session_changed";
            else if (!active_) reason = expired_ ? "lease_expired" : "session_closed";
            else if (!std::strcmp(request.operation, "close")) {
                active_ = false; expired_ = false;
                state = "closed"; reason = "session_closed";
            }
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
            else if (!std::strcmp(request.operation, "state")) {
                if (!provider_) reason = "state_unavailable";
                else {
                    const auto snapshot = provider_();
                    last_activity_ = now;
                    return deskbot_studio_runtime_query::Format(request.request_id, target_, boot_,
                        session_, ++sequence_, now, kLeaseMs, snapshot, output, capacity);
                }
            }
#endif
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
            else if (!std::strcmp(request.operation, "display")) {
                if (!display_provider_) reason = "display_unavailable";
                else {
                    const auto snapshot = display_provider_();
                    last_activity_ = now;
                    return deskbot_studio_display::Format(request.request_id, target_, boot_,
                        session_, ++sequence_, now, kLeaseMs, snapshot, output, capacity);
                }
            }
#endif
            else {
                last_activity_ = now;
                state = "ready"; reason = "session_alive";
            }
        }
        const bool expose = !std::strcmp(state, "ready") || !std::strcmp(state, "closed");
        char session_json[80] = "null", time_text[21];
        if (expose) std::snprintf(session_json, sizeof(session_json), "\"%s\"", session_);
        deskbot_studio_link::Decimal64(now, time_text);
        const int count = std::snprintf(output, capacity,
            "{\"schema\":\"%s\",\"message_type\":\"session\",\"request_id\":\"%s\","
            "\"target_id\":\"%s\",\"boot_id\":\"%s\",\"sequence\":%" PRIu32 ","
            "\"session_id\":%s,\"state\":\"%s\",\"reason\":\"%s\",\"at_ms\":%s,"
            "\"lease_ms\":%" PRIu32 ",\"controls_available\":false}",
            kSchema, request.request_id, target_, boot_, ++sequence_, session_json,
            state, reason, time_text, active_ && expose ? kLeaseMs : 0);
        return count > 0 && static_cast<size_t>(count) < capacity;
    }
private:
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
    deskbot_studio_display::Provider display_provider_ = nullptr;
#endif
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
    deskbot_studio_runtime_query::Provider provider_ = nullptr;
#endif
    char target_[40]{}, boot_[38]{}, session_[64]{}, nonce_[97]{};
    uint32_t epoch_ = 0, sequence_ = 0;
    uint64_t last_activity_ = 0, last_time_ = 0;
    bool active_ = false, expired_ = false;
};

class Endpoint {
public:
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
    void SetExpressionHooks(deskbot_studio_expression::Hooks hooks) { expression_ = hooks; }
#endif
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
    void SetDisplayProvider(deskbot_studio_display::Provider provider) { session_.SetDisplayProvider(provider); }
#endif
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
    void SetStateProvider(deskbot_studio_runtime_query::Provider provider) { session_.SetStateProvider(provider); }
#endif
    bool Initialize(const char* device_hex, const char* boot_hex) { return session_.Initialize(device_hex, boot_hex); }
    void Disconnect() {
        session_.Disconnect();
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
        if (expression_.disconnect) expression_.disconnect();
#endif
        framer_.Reset();
        partial_ = false;
        has_reply_ = false;
        last_byte_ = last_reply_ = 0;
    }
    void Tick(uint64_t now) {
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
        if (expression_.sync) expression_.sync(session_.ViewAt(now), now);
#endif
        if (partial_ && now >= last_byte_ && now - last_byte_ >= kPartialTimeoutMs) {
            framer_.Reset();
            partial_ = false;
        }
    }
    bool Feed(uint8_t byte, uint64_t now, char* output, size_t capacity) {
        Tick(now);
        last_byte_ = now;
        partial_ = byte != '\n';
        auto frame = framer_.Feed(byte);
        if (frame.kind != deskbot_studio_input::FrameKind::Line ||
            now > kMaxClock || (has_reply_ && (now < last_reply_ || now - last_reply_ < kReplyIntervalMs))) return false;
        Request request{};
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
        bool handled = false;
        if (Decoder(frame.data, frame.size).Read(request)) {
            handled = session_.Handle(request, now, output, capacity);
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
            if (expression_.sync) expression_.sync(session_.ViewAt(now), now);
#endif
        }
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
        else if (expression_.submit) {
            handled = expression_.submit(frame.data, frame.size, session_.ViewAt(now), now, output, capacity);
        }
#endif
        if (!handled) return false;
#else
        if (!Decoder(frame.data, frame.size).Read(request) || !session_.Handle(request, now, output, capacity)) return false;
#endif
        last_reply_ = now; has_reply_ = true;
        return true;
    }
private:
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
    deskbot_studio_expression::Hooks expression_{};
#endif
    deskbot_studio_input::RequestFramer framer_;
    Session session_;
    uint64_t last_byte_ = 0, last_reply_ = 0;
    bool partial_ = false, has_reply_ = false;
};
}  // namespace deskbot_studio_session
