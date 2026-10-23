#include "base_snapshot.h"

#include <cstring>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"
#include "vocat_base_control.h"

namespace base_snapshot {
namespace {

// vocat_base_control validates frames before invoking the callback and caps
// payloads at 249 bytes.  Keep the same upper bound at this observation
// boundary so malformed direct calls cannot truncate the length field.
constexpr int kMaxParserPayloadLength = 249;

static portMUX_TYPE s_snapshot_mux = portMUX_INITIALIZER_UNLOCKED;
static Snapshot s_snapshot{};

uint32_t NowMilliseconds()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000);
}

bool IsHeartbeatAlive(uint8_t cmd, const uint8_t* data, int data_len)
{
    return cmd == VOCAT_BASE_CMD_RECV_HEARTBEAT &&
           data != nullptr && data_len == 2 && data[0] == 0x00 && data[1] == 0x01;
}

}  // namespace

bool Observe(uint8_t cmd, const uint8_t* data, int data_len)
{
    if (data_len < 0 || data_len > kMaxParserPayloadLength ||
        (data_len > 0 && data == nullptr)) {
        return false;
    }

    const uint32_t now_ms = NowMilliseconds();
    const bool heartbeat = IsHeartbeatAlive(cmd, data, data_len);
    const uint8_t copied_len = static_cast<uint8_t>(data_len > 8 ? 8 : data_len);
    const bool truncated = data_len > 8;
    uint8_t payload[8]{};
    if (copied_len > 0) {
        std::memcpy(payload, data, copied_len);
    }

    // A zero timeout is intentional.  UART callback latency must not grow due
    // to an observer competing with a future reader; a busy observation is
    // simply dropped and never changes a partial snapshot.
    if (portTRY_ENTER_CRITICAL(&s_snapshot_mux, 0) != pdPASS) {
        return false;
    }

    s_snapshot.captured_frame_count++;
    s_snapshot.last_dispatch_complete_ms = now_ms;
    s_snapshot.last_cmd = cmd;
    s_snapshot.last_data_len = static_cast<uint8_t>(data_len);
    s_snapshot.copied_len = copied_len;
    s_snapshot.flags = static_cast<uint8_t>(
        (s_snapshot.flags | kFlagHasFrame) & ~kFlagLastFrameTruncated);
    if (truncated) {
        s_snapshot.flags = static_cast<uint8_t>(
            s_snapshot.flags | kFlagLastFrameTruncated);
    }
    std::memcpy(s_snapshot.last_payload, payload, sizeof(payload));
    if (heartbeat) {
        s_snapshot.captured_heartbeat_count++;
        s_snapshot.last_heartbeat_ms = now_ms;
        s_snapshot.flags = static_cast<uint8_t>(
            s_snapshot.flags | kFlagHasHeartbeat);
    }

    portEXIT_CRITICAL(&s_snapshot_mux);
    return true;
}

bool TryRead(Snapshot* out)
{
    if (out == nullptr) {
        return false;
    }

    if (portTRY_ENTER_CRITICAL(&s_snapshot_mux, 0) != pdPASS) {
        return false;
    }
    std::memcpy(out, &s_snapshot, sizeof(*out));
    portEXIT_CRITICAL(&s_snapshot_mux);
    return true;
}

}  // namespace base_snapshot
