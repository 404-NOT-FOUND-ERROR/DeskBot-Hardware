#pragma once
#include "studio_role_status.h"
#include "studio_expression_request.h"
#include "studio_device_link.h"
#include <cstdio>
#include <cstring>
#include <inttypes.h>

namespace deskbot_role_activation {
using Session = deskbot_studio_expression::SessionView;
constexpr uint64_t kQueueMs = 3000;
constexpr size_t kHistory = 16;
// The existing computer client ends each request ID with an increasing counter.
// Keep recent replies plus a high-water mark: older evicted IDs never run again.
inline bool RequestOrder(const char* request, char (&prefix)[97], uint32_t& order) {
    const auto* dash = std::strrchr(request, '-');
    if (!dash || dash == request || dash[1] < '1' || dash[1] > '9') return false;
    uint64_t value = 0;
    for (const char* p = dash + 1; *p; ++p) {
        if (*p < '0' || *p > '9' || value > UINT32_MAX / 10) return false;
        value = value * 10 + static_cast<unsigned>(*p - '0');
        if (value > UINT32_MAX) return false;
    }
    const auto size = static_cast<size_t>(dash - request);
    if (size >= sizeof(prefix)) return false;
    std::memcpy(prefix, request, size); prefix[size] = 0; order = static_cast<uint32_t>(value); return true;
}
struct Saved {
    uint32_t version = 1;
    char graph[65]{}, role[65]{};
    uint8_t reserved[2]{};
};
inline bool Hex(const char* value) {
    if (!value || std::strlen(value) != 64) return false;
    for (size_t i = 0; i < 64; ++i)
        if (!(value[i] >= '0' && value[i] <= '9') && !(value[i] >= 'a' && value[i] <= 'f')) return false;
    return true;
}
inline bool Operation(const char* op) { return op && (!std::strcmp(op, "role.query") || !std::strncmp(op, "role.activate.", 14)); }
inline bool Activation(const char* op, uint8_t& index, const char*& hash) {
    if (!op || std::strncmp(op, "role.activate.", 14) || std::strlen(op) != 80 ||
        op[14] < '0' || op[14] > '3' || op[15] != '.' || !Hex(op + 16)) return false;
    index = static_cast<uint8_t>(op[14] - '0'); hash = op + 16; return true;
}
struct Attempt {
    char request[97]{}, operation[97]{};
    uint64_t ticket = 0, queued_at = 0, selection = 0;
    uint8_t index = deskbot_role::kNoRole;
    const char* status = "idle";
    const char* reason = "none";
};
struct Reply {
    deskbot_role::Details device{};
    char request[97]{};
    const char* status = "idle", *reason = "none", *result = "state", *save = "none";
    uint8_t saved = deskbot_role::kNoRole;
};

// Caller serializes this state. No renderer, scheduler or NVS calls occur here.
class Control {
public:
    void Sync(const Session& view, uint64_t now) {
        const bool active = view.active && view.session && now < view.lease_deadline;
        if (!active || std::strcmp(session_, view.session)) {
            CancelQueued();
            if (active) std::snprintf(session_, sizeof(session_), "%s", view.session);
            else session_[0] = 0;
            count_ = 0;  // Dispatched work is tracked separately from deduplication.
            order_ = 0; prefix_[0] = 0;
        }
        active_ = active; lease_ = view.lease_deadline;
    }
    void Disconnect() { CancelQueued(); active_ = false; session_[0] = 0; count_ = 0; order_ = 0; prefix_[0] = 0; }
    uint8_t Mount(const deskbot_role::Details& device, const Saved* saved) {
        CancelQueued(); latest_ = {}; device_ = device; saved_ = {}; count_ = 0;
        saved_index_ = deskbot_role::kNoRole; save_ = "none"; save_attempted_ = false;
        if (!device.loaded) return deskbot_role::kNoRole;
        uint8_t initial = device.initial;
        if (saved && saved->version == 1 && saved->graph[64] == 0 && saved->role[64] == 0 &&
            Hex(saved->graph) && !std::strcmp(saved->graph, device.graph_sha256)) {
            for (uint8_t i = 0; i < device.count; ++i)
                if (!std::strcmp(saved->role, device.roles[i])) {
                    saved_ = *saved; saved_index_ = initial = i; save_ = "restored"; break;
                }
        }
        return initial;
    }
    uint64_t Submit(const char* op, const char* request, uint64_t now, Reply& out) {
        const char* result = "state", *rejection = nullptr;
        uint64_t queued = 0;
        if (std::strcmp(op, "role.query")) {
            uint8_t index = 0; const char* hash = nullptr;
            char prefix[97]{}; uint32_t order = 0;
            Attempt* duplicate = nullptr;
            for (size_t i = 0; i < count_; ++i)
                if (!std::strcmp(history_[i].request, request)) { duplicate = &history_[i]; break; }
            if (duplicate) {
                if (std::strcmp(duplicate->operation, op)) rejection = "request_conflict";
                else { Snapshot(out); CopyAttempt(*duplicate, out); out.result = "duplicate"; return 0; }
            } else if (!active_ || now >= lease_) rejection = "session_unavailable";
            else if (!Activation(op, index, hash)) rejection = "invalid_activation";
            else if (!device_.loaded) rejection = "roles_unavailable";
            else if (std::strcmp(hash, device_.graph_sha256)) rejection = "graph_changed";
            else if (index >= device_.count) rejection = "role_missing";
            else if (!RequestOrder(request, prefix, order)) rejection = "request_order_invalid";
            else if (order <= order_ || (prefix_[0] && std::strcmp(prefix_, prefix))) rejection = "request_order_expired";
            else {
                if (!std::strcmp(latest_.status, "queued") || !std::strcmp(latest_.status, "switching")) {
                    latest_.status = "cancelled"; latest_.reason = "target_replaced"; Remember();
                }
                latest_ = {};
                std::snprintf(latest_.request, sizeof(latest_.request), "%s", request);
                std::snprintf(latest_.operation, sizeof(latest_.operation), "%s", op);
                latest_.ticket = ++serial_; latest_.queued_at = now; latest_.index = index;
                latest_.status = "queued"; latest_.reason = "application_queued";
                if (count_ == kHistory) {
                    std::memmove(history_, history_ + 1, (kHistory - 1) * sizeof(Attempt)); --count_;
                }
                order_ = order; std::strcpy(prefix_, prefix);
                history_[count_++] = latest_; queued = latest_.ticket; result = "accepted";
            }
        }
        Snapshot(out);
        out.result = rejection ? "rejected" : result;
        if (rejection) out.reason = rejection;
        return queued;
    }
    bool Begin(uint64_t ticket, uint64_t now, Saved& target) {
        if (latest_.ticket != ticket || std::strcmp(latest_.status, "queued")) return false;
        if (!active_ || now >= lease_ || now < latest_.queued_at || now - latest_.queued_at >= kQueueMs) {
            latest_.status = "cancelled"; latest_.reason = "request_expired"; Remember(); return false;
        }
        latest_.status = "switching"; latest_.reason = "awaiting_first_frame";
        save_attempted_ = false; save_ = "pending";
        target = {}; std::strcpy(target.graph, device_.graph_sha256);
        std::strcpy(target.role, device_.roles[latest_.index]); Remember(); return true;
    }
    void Dispatched(uint64_t ticket, const deskbot_role::Details& device, bool ok) {
        if (ticket != latest_.ticket) return;
        device_ = device;
        if (ok) latest_.selection = device.selection;
        else { latest_.status = "failed"; latest_.reason = "player_unavailable"; save_ = "none"; }
        Remember();
    }
    bool Observe(const deskbot_role::Details& device, Saved& save, uint64_t& token) {
        device_ = device;
        if (!device.loaded && (!std::strcmp(latest_.status, "queued") || !std::strcmp(latest_.status, "switching"))) {
            latest_.status = "cancelled"; latest_.reason = "roles_unavailable";
            save_ = "none"; Remember(); return false;
        }
        if (device.failed && !std::strcmp(latest_.status, "switching") && device.selection == latest_.selection) {
            latest_.status = "failed"; latest_.reason = "frame_preparation_failed"; save_ = "none"; Remember(); return false;
        }
        if (device.loaded && !std::strcmp(latest_.status, "switching") &&
            device.selection == latest_.selection && device.phase == deskbot_role::Phase::None &&
            device.current == deskbot_role::kNoRole) {
            // A built-in semantic outside this package took over before the new
            // role's first frame. Finish this attempt; a later click can retry.
            latest_.status = "cancelled"; latest_.reason = "expression_interrupted";
            save_ = "none"; Remember(); return false;
        }
        if (!device.loaded || std::strcmp(latest_.status, "switching") || !latest_.selection ||
            device.selection != latest_.selection || device.rendered_selection != latest_.selection ||
            device.rendered != latest_.index || device.target != latest_.index) return false;
        latest_.status = "applied"; latest_.reason = "first_frame_executed"; Remember();
        if (saved_index_ == latest_.index && !std::strcmp(saved_.graph, device.graph_sha256)) {
            save_ = "saved"; return false;
        }
        if (save_attempted_) return false;
        save_attempted_ = true; save = {};
        std::strcpy(save.graph, device.graph_sha256); std::strcpy(save.role, device.roles[device.target]);
        token = latest_.ticket; return true;
    }
    void SavedResult(uint64_t token, const Saved& saved, bool ok) {
        if (ok) {
            saved_ = saved; saved_index_ = deskbot_role::kNoRole;
            if (device_.loaded && !std::strcmp(saved.graph, device_.graph_sha256))
                for (uint8_t i = 0; i < device_.count; ++i)
                    if (!std::strcmp(saved.role, device_.roles[i])) saved_index_ = i;
        }
        if (latest_.ticket == token) save_ = ok ? "saved" : "failed";
    }
    void Snapshot(Reply& out) const {
        out = {}; out.device = device_; out.saved = saved_index_; out.save = save_; CopyAttempt(latest_, out);
    }
private:
    static void CopyAttempt(const Attempt& value, Reply& out) {
        std::strcpy(out.request, value.request); out.status = value.status; out.reason = value.reason;
    }
    void Remember() { for (size_t i = 0; i < count_; ++i) if (history_[i].ticket == latest_.ticket) history_[i] = latest_; }
    void CancelQueued() {
        if (!std::strcmp(latest_.status, "queued")) {
            latest_.status = "cancelled"; latest_.reason = "session_changed"; Remember();
        }
    }
    deskbot_role::Details device_{};
    Saved saved_{};
    Attempt latest_{}, history_[kHistory]{};
    char session_[97]{}, prefix_[97]{};
    uint32_t order_ = 0;
    uint64_t serial_ = 0, lease_ = 0;
    size_t count_ = 0;
    uint8_t saved_index_ = deskbot_role::kNoRole;
    const char* save_ = "none";
    bool active_ = false, save_attempted_ = false;
};

inline int Index(uint8_t index) { return index == deskbot_role::kNoRole ? -1 : int(index); }
inline bool Format(const Reply& row, const char* request, const char* target, const char* boot,
                   const char* session, uint32_t sequence, uint64_t now, char* output, size_t capacity) {
    if (!output || capacity < 2048) return false;
    char roles[280]{}; size_t used = 0;
    for (uint8_t i = 0; i < row.device.count; ++i) {
        const int n = std::snprintf(roles + used, sizeof(roles) - used, "%s\"%s\"", i ? "," : "", row.device.roles[i]);
        if (n <= 0 || size_t(n) >= sizeof(roles) - used) return false;
        used += size_t(n);
    }
    constexpr const char* phases[] = {"none", "single", "entry", "loop", "exit", "held"};
    const auto& d = row.device;
    char at_text[21];
    deskbot_studio_link::Decimal64(now, at_text);
    const int n = std::snprintf(output, capacity,
        "{\"schema\":\"deskbot.studio_roles/v1\",\"message_type\":\"roles\",\"request_id\":\"%s\","
        "\"target_id\":\"%s\",\"boot_id\":\"%s\",\"session_id\":\"%s\",\"sequence\":%" PRIu32 ","
        "\"at_ms\":%s,\"lease_ms\":30000,\"result\":\"%s\",\"reason\":\"%s\","
        "\"loaded\":%s,\"graph_sha256\":\"%s\",\"roles\":[%s],\"default_index\":%d,"
        "\"target_index\":%d,\"current_index\":%d,\"active_index\":%d,\"phase\":\"%s\",\"blocked\":%u,"
        "\"sleep\":\"%s\",\"saved_index\":%d,\"save_state\":\"%s\","
        "\"activation\":{\"request_id\":\"%s\",\"status\":\"%s\"}}",
        request, target, boot, session, sequence, at_text, row.result, row.reason, d.loaded ? "true" : "false",
        d.graph_sha256, roles, Index(d.initial), Index(d.target), Index(d.current), Index(d.rendered),
        phases[static_cast<uint8_t>(d.phase)], unsigned(d.blocked),
        d.sleep_known ? (d.asleep ? "sleeping" : "awake") : "unknown", Index(row.saved), row.save, row.request, row.status);
    return n > 0 && size_t(n) < capacity;
}
using Provider = bool (*)(const char*, const char*, const Session&, uint64_t, Reply&);
struct Hooks { Provider request = nullptr; void (*sync)(const Session&, uint64_t) = nullptr; void (*disconnect)() = nullptr; };
Hooks AdapterHooks();
void Mounted(void* handle);
void Observe(void* handle);
}  // namespace deskbot_role_activation
