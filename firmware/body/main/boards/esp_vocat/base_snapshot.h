#ifndef BASE_SNAPSHOT_H
#define BASE_SNAPSHOT_H

#include <cstdint>
#include <type_traits>

namespace base_snapshot {

// A single, fixed-size observation of the most recently dispatched base
// frame.  The UART parser owns the input buffer; this module only copies data
// during Observe() and never retains a pointer into that buffer.
struct Snapshot {
    uint32_t captured_frame_count;
    uint32_t captured_heartbeat_count;
    uint32_t last_dispatch_complete_ms;
    uint32_t last_heartbeat_ms;
    uint8_t last_cmd;
    uint8_t last_data_len;
    uint8_t copied_len;
    uint8_t flags;
    uint8_t last_payload[8];
};

static_assert(sizeof(Snapshot) == 28, "base snapshot ABI must remain 28 bytes");
static_assert(std::is_trivial<Snapshot>::value &&
              std::is_standard_layout<Snapshot>::value,
              "base snapshot must remain a POD");

enum : uint8_t {
    kFlagHasFrame = 1u << 0,
    kFlagHasHeartbeat = 1u << 1,
    kFlagLastFrameTruncated = 1u << 2,
};

// Observe one frame after the official BaseControl::HandleCommand() returns.
// The data pointer is borrowed only for this call.  Returns false for an
// invalid callback shape (negative length, length > parser maximum, or a null
// pointer paired with a non-zero length), or when the zero-wait acquisition is
// busy.  A zero-length frame is valid.  All rejected observations leave the
// snapshot unchanged.
bool Observe(uint8_t cmd, const uint8_t* data, int data_len);

// Copy the current snapshot without waiting.  Returns false when out is null
// or the zero-wait critical-section acquisition is busy; in either case the
// caller's output object is left untouched.
bool TryRead(Snapshot* out);

}  // namespace base_snapshot

#endif  // BASE_SNAPSHOT_H
