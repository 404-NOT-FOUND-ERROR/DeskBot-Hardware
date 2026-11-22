#pragma once

#include "studio_device_link.h"
#include "studio_role_status.h"
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <inttypes.h>

namespace deskbot_wifi_expression {
constexpr uint64_t kMaxNumber = 9007199254740991ULL;
constexpr size_t kReplyBytes = 384;

inline bool Hex(const char* s, size_t n) {
    if (!s || std::strlen(s) != n) return false;
    for (size_t i = 0; i < n; ++i)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return false;
    return true;
}
inline bool Identifier(const char* s) {
    if (!s || !s[0] || std::strlen(s) > 64) return false;
    const auto alpha = [](char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); };
    if (!alpha(*s)) return false;
    for (++s; *s; ++s) if (!alpha(*s) && !(*s >= '0' && *s <= '9')
        && *s != '-' && *s != '_' && *s != '.' && *s != ':') return false;
    return true;
}
inline bool Number(const char* s, uint64_t& value) {
    value = 0;
    if (!s || !*s || (*s == '0' && s[1])) return false;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9' || value > (kMaxNumber - (*s - '0')) / 10) return false;
        value = value * 10 + (*s - '0');
    }
    return true;
}

struct Command {
    char id[29]{}, boot[38]{}, graph[65]{}, role[65]{}, expression[8]{};
    uint64_t sequence = 0, query_ms = 0, ttl_ms = 0, selection = 0;
};
// Ten bounded ASCII fields, one newline, no escapes or heap allocation.
inline bool Decode(const char* data, size_t size, Command& out) {
    if (!data || !size || size >= kReplyBytes || data[size - 1] != '\n') return false;
    char text[kReplyBytes]{};
    std::memcpy(text, data, size - 1);
    char* tokens[10]{text};
    size_t count = 1;
    for (size_t i = 0; i + 1 < size; ++i) {
        if (text[i] == '|') {
            if (count == 10) return false;
            text[i] = 0; tokens[count++] = text + i + 1;
        } else if (text[i] < 33 || text[i] > 126) return false;
    }
    if (count != 10 || std::strcmp(tokens[0], "v1") || std::strlen(tokens[1]) != 28
        || std::strncmp(tokens[1], "cmd-", 4) || !Hex(tokens[1] + 4, 24)
        || std::strlen(tokens[3]) != 37 || std::strncmp(tokens[3], "boot-", 5) || !Hex(tokens[3] + 5, 32)
        || !Hex(tokens[6], 64) || !Identifier(tokens[7])
        || (std::strcmp(tokens[9], "neutral") && std::strcmp(tokens[9], "happy"))) return false;
    Command candidate{};
    if (!Number(tokens[2], candidate.sequence) || !candidate.sequence
        || !Number(tokens[4], candidate.query_ms) || !Number(tokens[5], candidate.ttl_ms)
        || !candidate.ttl_ms || candidate.ttl_ms > 3000 || !Number(tokens[8], candidate.selection)) return false;
    std::strcpy(candidate.id, tokens[1]); std::strcpy(candidate.boot, tokens[3]);
    std::strcpy(candidate.graph, tokens[6]); std::strcpy(candidate.role, tokens[7]);
    std::strcpy(candidate.expression, tokens[9]); out = candidate;
    return true;
}

struct Binding {
    char graph[65]{}, role[65]{};
    uint64_t selection = 0;
    bool available = false;
};
inline Binding CopyBinding(const deskbot_role::Details& d) {
    Binding out{};
    if (!d.loaded || d.target >= d.count || d.count > 4 || !Hex(d.graph_sha256, 64)
        || !Identifier(d.roles[d.target]) || d.selection > kMaxNumber) return out;
    std::strcpy(out.graph, d.graph_sha256); std::strcpy(out.role, d.roles[d.target]);
    out.selection = d.selection;
    out.available = !d.failed && !d.blocked && !d.asleep && d.phase != deskbot_role::Phase::None;
    return out;
}
enum class Result : uint8_t { None, Dispatched, OwnerBusy, UsbBusy, BindingChanged, Expired, PlayerUnavailable };
inline const char* Status(Result result) {
    switch (result) {
        case Result::Dispatched: return "dispatched";
        case Result::OwnerBusy: case Result::UsbBusy: return "busy";
        case Result::BindingChanged: return "interrupted";
        case Result::Expired: return "expired";
        default: return "rejected";
    }
}
inline const char* Reason(Result result) {
    switch (result) {
        case Result::Dispatched: return "submitted_to_player";
        case Result::OwnerBusy: return "owner_busy";
        case Result::UsbBusy: return "usb_control_active";
        case Result::BindingChanged: return "binding_changed";
        case Result::Expired: return "expired";
        default: return "player_unavailable";
    }
}
struct Receipt { char id[29]{}; uint64_t sequence = 0; Result result = Result::None; };
struct View { Binding binding{}; bool owner_ready = false, usb_active = false; };

class Control {
public:
    bool Accept(const Command& cmd, const char* boot, uint64_t query, uint64_t now, const View& view, uint64_t& ticket) {
        ticket = 0;
        if (std::strcmp(cmd.boot, boot) || cmd.query_ms != query || now < query) return false;
        if (cmd.sequence < command_.sequence) return false;
        if (cmd.sequence == command_.sequence) {
            if (std::strcmp(cmd.id, command_.id) || std::strcmp(cmd.graph, command_.graph)
                || std::strcmp(cmd.role, command_.role) || cmd.selection != command_.selection
                || std::strcmp(cmd.expression, command_.expression)) return false;
            if (result_ != Result::None) receipt_ready_ = true;
            return true;
        }
        // Keep a single pending/receipt slot until the peer confirms its result.
        if (pending_ || receipt_ready_ || queued_) return false;
        command_ = cmd; result_ = Result::None; executing_ = false; pending_ = true;
        deadline_ = query + cmd.ttl_ms;
        if (const auto reason = Check(now, view); reason != Result::None) SetResult(reason);
        else { ticket = cmd.sequence; queued_ = true; }
        return true;
    }
    bool Begin(uint64_t ticket, uint64_t now, const View& view, char (&semantic)[8]) {
        if (ticket != command_.sequence || !queued_) return false;
        // A timed-out callback retains its one scheduler slot until drained.
        // Its ACK can be sent meanwhile; a new command cannot add another slot.
        queued_ = false;
        if (!pending_ || executing_) return false;
        if (const auto reason = Check(now, view); reason != Result::None) { SetResult(reason); return false; }
        executing_ = true; std::strcpy(semantic, command_.expression); return true;
    }
    void Complete(uint64_t ticket, bool submitted) {
        if (pending_ && executing_ && ticket == command_.sequence)
            SetResult(submitted ? Result::Dispatched : Result::PlayerUnavailable);
    }
    void Expire(uint64_t now) { if (pending_ && !executing_ && now >= deadline_) SetResult(Result::Expired); }
    Receipt ReadReceipt() const {
        Receipt out{};
        if (receipt_ready_) { std::strcpy(out.id, command_.id); out.sequence = command_.sequence; out.result = result_; }
        return out;
    }
    void Confirm(const Receipt& sent) {
        if (sent.result != Result::None && sent.sequence == command_.sequence && !std::strcmp(sent.id, command_.id)
            && sent.result == result_) receipt_ready_ = false;
    }
    bool Available() const { return !pending_ && !receipt_ready_ && !queued_; }
private:
    Result Check(uint64_t now, const View& view) const {
        if (now < command_.query_ms || now >= deadline_) return Result::Expired;
        if (std::strcmp(command_.graph, view.binding.graph) || std::strcmp(command_.role, view.binding.role)
            || command_.selection != view.binding.selection) return Result::BindingChanged;
        if (view.usb_active) return Result::UsbBusy;
        if (!view.owner_ready) return Result::OwnerBusy;
        return view.binding.available ? Result::None : Result::PlayerUnavailable;
    }
    void SetResult(Result result) { result_ = result; pending_ = executing_ = false; receipt_ready_ = true; }
    Command command_{};
    uint64_t deadline_ = 0;
    Result result_ = Result::None;
    bool pending_ = false, executing_ = false, receipt_ready_ = false, queued_ = false;
};

inline int Encode(char* out, size_t size, const char* device, const char* boot, const char* firmware,
                  uint64_t now, const Binding& binding, bool ready, const Receipt& receipt) {
    if (!Hex(device, 32) || !Hex(boot, 32) || !Hex(firmware, 64) || now > kMaxNumber) return 0;
    char sequence_text[21], now_text[21], selection_text[21];
    deskbot_studio_link::Decimal64(receipt.sequence, sequence_text);
    deskbot_studio_link::Decimal64(now, now_text);
    deskbot_studio_link::Decimal64(binding.selection, selection_text);
    char ack[200] = "null";
    if (receipt.result != Result::None) {
        std::snprintf(ack, sizeof(ack), "{\"command_id\":\"%s\",\"sequence\":%s,\"status\":\"%s\",\"reason\":\"%s\"}",
            receipt.id, sequence_text, Status(receipt.result), Reason(receipt.result));
    }
    const int n = std::snprintf(out, size,
        "{\"device_id\":\"device-%s\",\"boot_id\":\"boot-%s\",\"firmware_sha256\":\"%s\",\"uptime_ms\":%s,"
        "\"ready\":%s,\"graph_sha256\":\"%s\",\"role_id\":\"%s\",\"selection\":%s,\"receipt\":%s}",
        device, boot, firmware, now_text, ready ? "true" : "false", binding.graph, binding.role, selection_text, ack);
    return n > 0 && static_cast<size_t>(n) < size ? n : 0;
}

#if __has_include("wifi_expression.local.h")
bool Prepare(const char* device, const char* boot, const char* firmware);
bool Poll();
void Start(const char* device, const char* boot, const char* firmware);
void Observe(const deskbot_role::Details& details);
void UsbLease(bool active, uint64_t deadline);
#else
inline void Start(const char*, const char*, const char*) {}
inline void Observe(const deskbot_role::Details&) {}
inline void UsbLease(bool, uint64_t) {}
#endif
static_assert(sizeof(Control) <= 320, "one bounded command and receipt");
}  // namespace deskbot_wifi_expression
