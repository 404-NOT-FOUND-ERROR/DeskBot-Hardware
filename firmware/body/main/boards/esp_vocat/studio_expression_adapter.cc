#include "sdkconfig.h"

#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
#include "studio_expression_adapter.h"
#include "studio_expression_control.h"
#include "studio_runtime_state.h"
#include "application.h"
#include "board.h"
#include "display/emote_display.h"
#include <esp_timer.h>
#include <mbedtls/sha256.h>
#include <mutex>

namespace deskbot_studio_expression {
namespace {
Control control;
std::mutex mutex;
// Only the existing USB input owner uses this scratch space. Keep parsing,
// hashing and the immediate receipt off its 6144-byte task stack.
struct InputWorkspace {
    Request request{};
    uint8_t material[800]{}, digest[32]{};
    Receipt receipt{};
} input;
uint64_t Now() { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }
void SynchronizeSession(const SessionView& view, uint64_t) {
    std::lock_guard<std::mutex> guard(mutex);
    control.Synchronize(view, Now());
}
void DisconnectSession() {
    std::lock_guard<std::mutex> guard(mutex);
    control.Disconnect(Now());
}
void Dispatch(uint32_t ticket) {
    {
        std::lock_guard<std::mutex> guard(mutex);
        if (!control.Begin(ticket, Now(), deskbot_studio_display::Reason(deskbot_studio_runtime::ReadDisplay()))) return;
    }
    auto* display = dynamic_cast<emote::EmoteDisplay*>(Board::GetInstance().GetDisplay());
    const bool submitted = display && display->GetEmoteHandle();
    if (submitted) display->SetEmotion("neutral");
    {
        std::lock_guard<std::mutex> guard(mutex);
        control.Complete(ticket, Now(), submitted);
    }
}
bool SubmitFrame(const uint8_t* data, size_t size, const SessionView& view,
                 uint64_t, char* output, size_t capacity) {
    if (!output || capacity < 1024) return false;
    const auto received_at = Now();
    auto& request = input.request; request = {};
    if (!Decoder(data, size).Read(request)) return false;
    const size_t count = FingerprintMaterial(request, input.material);
    if (!count || mbedtls_sha256(input.material, count, input.digest, 0)) return false;
    auto& receipt = input.receipt;
    uint32_t ticket = 0;
    {
        std::lock_guard<std::mutex> guard(mutex);
        const auto now = Now();
        control.Synchronize(view, now);
        // Identity/session checks precede even a mirrored readiness read.
        const bool matches = view.active && now < view.lease_deadline &&
            !std::strcmp(request.target_id, view.target) && !std::strcmp(request.boot_id, view.boot) &&
            !std::strcmp(request.session_id, view.session);
        if (!view.session[0]) return false;
        const char* reason = matches ? deskbot_studio_display::Reason(deskbot_studio_runtime::ReadDisplay()) : "session_unavailable";
        if (!control.Submit(request, input.digest, received_at, now, reason, receipt, ticket)) return false;
    }
    // No new lock spans the scheduler, LVGL or player API.
    if (ticket) Application::GetInstance().Schedule([ticket] { Dispatch(ticket); });
    return Format(receipt, output, capacity);
}
}  // namespace
Hooks AdapterHooks() { return {SubmitFrame, SynchronizeSession, DisconnectSession}; }
bool Poll(char* output, size_t capacity) {
    if (!output || capacity < 1024) return false;
    Receipt receipt;
    {
        std::lock_guard<std::mutex> guard(mutex);
        if (!control.Poll(receipt, Now())) return false;
    }
    return Format(receipt, output, capacity);
}
}  // namespace deskbot_studio_expression
#endif
