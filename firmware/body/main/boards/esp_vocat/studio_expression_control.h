#pragma once

#include "studio_expression_request.h"
#include "studio_display_readiness.h"

namespace deskbot_studio_expression {
enum class Status : uint8_t { Accepted, Dispatched, Busy, Rejected, Interrupted, Expired, Duplicate };
inline const char* Name(Status status) {
    const char* names[] = {"accepted", "dispatched", "busy", "rejected", "interrupted", "expired", "duplicate"};
    return names[static_cast<unsigned>(status)];
}
struct Receipt {
    char request[97]{}, target[97]{}, boot[97]{}, session[97]{};
    uint64_t request_sequence = 0, sequence = 0, at = 0;
    Status status = Status::Rejected, original = Status::Rejected;
    const char* reason = "unavailable";
};
inline bool Format(const Receipt& receipt, char* output, size_t capacity) {
    if (!output || capacity < 1024) return false;
    char sequence[21], request_sequence[21], at[21], original[32] = "null";
    deskbot_studio_link::Decimal64(receipt.sequence, sequence);
    deskbot_studio_link::Decimal64(receipt.request_sequence, request_sequence);
    deskbot_studio_link::Decimal64(receipt.at, at);
    if (receipt.status == Status::Duplicate)
        std::snprintf(original, sizeof(original), "\"%s\"", Name(receipt.original));
    const int count = std::snprintf(output, capacity,
        "{\"schema\":\"%s\",\"message_type\":\"receipt\",\"target_id\":\"%s\",\"boot_id\":\"%s\","
        "\"session_id\":\"%s\",\"sequence\":%s,\"request_id\":\"%s\",\"request_sequence\":%s,"
        "\"status\":\"%s\",\"reason\":\"%s\",\"source\":\"device\",\"correlated\":true,"
        "\"original_status\":%s,\"at_ms\":%s}", kSchema, receipt.target, receipt.boot, receipt.session,
        sequence, receipt.request, request_sequence, Name(receipt.status), receipt.reason, original, at);
    return count > 0 && static_cast<size_t>(count) < capacity;
}

// Stable fingerprint material: NUL-separated decoded fields in fixed order.
// Canonical decimal integers and explicit kind make field order/JSON escapes
// irrelevant. The adapter hashes these bytes with the existing SHA-256 library.
inline size_t FingerprintMaterial(const Request& r, uint8_t (&bytes)[800]) {
    char sequence[21], ttl[21];
    deskbot_studio_link::Decimal64(r.sequence, sequence);
    deskbot_studio_link::Decimal64(r.ttl_ms, ttl);
    const char* fields[] = {r.request_id, r.target_id, r.boot_id, r.session_id, r.catalog_id,
        r.role_id, r.issued_at, sequence, ttl, r.kind == Kind::Action ? "action" : "expression", r.command_id};
    size_t count = 0;
    for (const auto* field : fields) {
        const size_t size = std::strlen(field) + 1;
        if (count + size > sizeof(bytes)) return 0;
        std::memcpy(bytes + count, field, size); count += size;
    }
    return count;
}

// Caller synchronizes all methods. No scheduler, UI, clock, heap or hardware.
// One reserved callback survives cancellation until that callback is consumed.
class Control {
public:
    void Synchronize(const SessionView& view, uint64_t now) {
        if (now > kMaxCounter || now < last_time_) return;
        last_time_ = now;
        const bool changed = epoch_ != view.epoch || std::strcmp(session_, view.session);
        if (pending_ && !executing_ && (changed || !view.active || now >= view.lease_deadline))
            Finish(Status::Interrupted, changed ? "session_changed" : "session_closed", now);
        if (changed) { for (auto& row : ledger_) row = {}; high_water_ = 0; next_ = 0; }
        Copy(target_, view.target); Copy(boot_, view.boot); Copy(session_, view.session);
        epoch_ = view.epoch; lease_ = view.lease_deadline; active_ = view.active;
        if (pending_ && !executing_ && now >= deadline_)
            Finish(Status::Expired, "deadline_before_dispatch", now);
    }
    void Disconnect(uint64_t now) {
        active_ = false;
        if (pending_ && !executing_) Finish(Status::Interrupted, "session_closed", now);
    }
    bool Submit(const Request& request, const uint8_t (&digest)[32], uint64_t received_at, uint64_t now,
                const char* readiness, Receipt& reply, uint32_t& ticket) {
        ticket = 0;
        if (now > kMaxCounter || now < last_time_ || receipt_sequence_ >= kMaxCounter - 1) return false;
        last_time_ = now;
        const auto reject = [&](const char* reason) { reply = Make(request, Status::Rejected, reason, now); return true; };
        if (std::strcmp(request.target_id, target_)) return reject("target_mismatch");
        if (std::strcmp(request.boot_id, boot_)) return reject("boot_changed");
        if (std::strcmp(request.session_id, session_)) return reject("session_changed");
        if (!active_ || now >= lease_) return reject("lease_expired");
        for (const auto& row : ledger_) if (row.used && !std::strcmp(row.id, request.request_id)) {
            if (std::memcmp(row.digest, digest, 32)) return reject("request_id_conflict");
            reply = Make(request, Status::Duplicate, "replayed_receipt", now);
            reply.original = row.status; return true;
        }
        if (std::strcmp(request.catalog_id, kCatalog) || std::strcmp(request.role_id, kRole))
            return reject("catalog_or_role_changed");
        if (request.sequence <= high_water_) return reject("sequence_not_increasing");
        if (received_at > now || received_at > kMaxCounter - request.ttl_ms) return reject("deadline_out_of_range");
        high_water_ = request.sequence;
        // Rotate the bounded ledger; the pending decision remains retained.
        if (pending_ && ledger_[next_].used && !std::strcmp(ledger_[next_].id, request_.request_id))
            next_ = (next_ + 1) % 64;
        auto& row = ledger_[next_]; row = {}; row.used = true;
        Copy(row.id, request.request_id); std::memcpy(row.digest, digest, 32);
        next_ = (next_ + 1) % 64;
        Status status = Status::Accepted; const char* reason = "queued_for_owner";
        if (request.kind != Kind::Expression || std::strcmp(request.command_id, "neutral")) {
            status = Status::Rejected; reason = "unsupported_expression_or_action";
        } else if (reserved_ || pending_ || terminal_) {
            status = Status::Busy; reason = "existing_owner_or_request";
        } else if (std::strcmp(readiness, "sleep_unknown")) {
            status = Status::Busy; reason = readiness;
        } else if (ticket_counter_ == UINT32_MAX) {
            status = Status::Rejected; reason = "ticket_counter_exhausted";
        } else if (now >= received_at + request.ttl_ms) {
            status = Status::Expired; reason = "deadline_before_dispatch";
        } else {
            request_ = request; deadline_ = received_at + request.ttl_ms; pending_ = reserved_ = true;
            ticket = ++ticket_counter_; pending_ticket_ = ticket; pending_epoch_ = epoch_;
        }
        row.status = status;
        reply = Make(request, status, reason, now);
        if (status == Status::Accepted) accepted_ = reply;
        return true;
    }
    bool Begin(uint32_t ticket, uint64_t now, const char* readiness) {
        if (!reserved_ || ticket != pending_ticket_) return false;
        if (!pending_) { reserved_ = false; return false; }
        if (now > kMaxCounter || now < last_time_) { Cancel("clock_out_of_range"); return false; }
        last_time_ = now;
        if (!active_ || epoch_ != pending_epoch_ || now >= lease_) {
            Finish(Status::Interrupted, "session_changed_before_dispatch", now); reserved_ = false; return false;
        }
        if (now >= deadline_) { Finish(Status::Expired, "deadline_before_dispatch", now); reserved_ = false; return false; }
        if (std::strcmp(readiness, "sleep_unknown")) {
            Finish(Status::Interrupted, "owner_changed_before_dispatch", now); reserved_ = false; return false;
        }
        executing_ = true; return true;
    }
    void Complete(uint32_t ticket, uint64_t now, bool submitted) {
        if (!reserved_ || !executing_ || ticket != pending_ticket_) return;
        // Begin is the commit point. A later reconnect cannot undo a call.
        Finish(submitted ? Status::Dispatched : Status::Interrupted,
               submitted ? "submitted_to_display" : "player_unavailable", now);
        reserved_ = executing_ = false;
    }
    bool Poll(Receipt& reply, uint64_t now) {
        if (!terminal_ || now > kMaxCounter || now < last_time_) return false;
        last_time_ = now;
        reply = terminal_receipt_; reply.sequence = ++receipt_sequence_; reply.at = now;
        terminal_ = false; return true;
    }
private:
    template<size_t N> static void Copy(char (&out)[N], const char* value) {
        std::snprintf(out, N, "%s", value);
    }
    Receipt Make(const Request& request, Status status, const char* reason, uint64_t now) {
        Receipt result;
        Copy(result.request, request.request_id); Copy(result.target, target_);
        Copy(result.boot, boot_); Copy(result.session, session_);
        result.request_sequence = request.sequence; result.sequence = ++receipt_sequence_;
        result.at = now; result.status = status; result.reason = reason; return result;
    }
    void Cancel(const char* reason) { Finish(Status::Interrupted, reason, last_time_); reserved_ = false; }
    void Finish(Status status, const char* reason, uint64_t now) {
        terminal_receipt_ = accepted_;
        terminal_receipt_.sequence = 0; // Assigned by the USB owner when emitted.
        terminal_receipt_.at = now; terminal_receipt_.status = status; terminal_receipt_.reason = reason;
        terminal_ = true; pending_ = false;
        for (auto& row : ledger_) if (row.used && pending_epoch_ == epoch_ && !std::strcmp(row.id, request_.request_id))
            row.status = status;
    }
    struct Entry { char id[97]{}; uint8_t digest[32]{}; Status status = Status::Rejected; bool used = false; };
    Entry ledger_[64]{};
    Request request_{};
    Receipt accepted_{}, terminal_receipt_{};
    char target_[97]{}, boot_[97]{}, session_[97]{};
    uint64_t lease_ = 0, deadline_ = 0, high_water_ = 0, receipt_sequence_ = 0, last_time_ = 0;
    uint32_t epoch_ = 0, ticket_counter_ = 0, pending_ticket_ = 0, pending_epoch_ = 0;
    size_t next_ = 0;
    bool active_ = false, pending_ = false, reserved_ = false, executing_ = false, terminal_ = false;
};
static_assert(sizeof(Control) <= 12000, "bounded expression request and 64-entry ledger");
}  // namespace deskbot_studio_expression
