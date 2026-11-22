#include "sdkconfig.h"
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
#include "studio_role_activation.h"
#include "studio_role_player.h"
#include "wifi_expression.h"
#include "application.h"
#include "board.h"
#include "display/emote_display.h"
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <nvs.h>
#include <mutex>
#include <new>

namespace deskbot_role_activation {
namespace {
std::mutex mutex;
Control* control = nullptr;  // Ledger and copied directory live in PSRAM.
uint64_t Now() { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }
bool Ensure() {
    if (control) return true;
    auto* memory = heap_caps_malloc(sizeof(Control), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (memory) control = new (memory) Control{};
    return control != nullptr;
}
bool Load(Saved& row) {
    nvs_handle_t handle = 0;
    if (nvs_open("studio_role", NVS_READONLY, &handle) != ESP_OK) return false;
    size_t size = sizeof(row);
    const auto result = nvs_get_blob(handle, "choice", &row, &size);
    nvs_close(handle);
    return result == ESP_OK && size == sizeof(row);
}
bool Save(const Saved& row) {
    nvs_handle_t handle = 0;
    if (nvs_open("studio_role", NVS_READWRITE, &handle) != ESP_OK) return false;
    auto result = nvs_set_blob(handle, "choice", &row, sizeof(row));
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
    return result == ESP_OK;
}
void Dispatch(uint64_t ticket) {
    Saved target{};
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (!control || !control->Begin(ticket, Now(), target)) return;
    }
    auto* display = dynamic_cast<emote::EmoteDisplay*>(Board::GetInstance().GetDisplay());
    const auto handle = display ? display->GetEmoteHandle() : nullptr;
    auto device = deskbot_role::Describe(handle);
    // A queued request must still address the same mounted graph at execution.
    const bool ok = device.loaded && !std::strcmp(device.graph_sha256, target.graph) &&
                    deskbot_role::Select(handle, target.role);
    device = deskbot_role::Describe(handle);
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (control) control->Dispatched(ticket, device, ok);
    }
    Observe(handle);
}
bool Request(const char* op, const char* request, const Session& view, uint64_t now, Reply& reply) {
    uint64_t ticket = 0;
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (!Ensure()) return false;
        control->Sync(view, now);
        ticket = control->Submit(op, request, now, reply);
    }
    // No role state lock spans application scheduling, rendering, or Flash I/O.
    if (ticket) Application::GetInstance().Schedule([ticket] { Dispatch(ticket); });
    return true;
}
void Sync(const Session& view, uint64_t now) {
    std::lock_guard<std::mutex> lock(mutex);
    if (control) control->Sync(view, now);
}
void Disconnect() {
    std::lock_guard<std::mutex> lock(mutex);
    if (control) control->Disconnect();
}
}  // namespace
Hooks AdapterHooks() { return {Request, Sync, Disconnect}; }
void Mounted(void* handle) {
    const auto emote = static_cast<emote_handle_t>(handle);
    const auto device = deskbot_role::Describe(emote);
    Saved saved{};
    const bool found = Load(saved);
    uint8_t index = deskbot_role::kNoRole;
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (!Ensure()) return;
        index = control->Mount(device, found ? &saved : nullptr);
    }
    if (index < device.count) deskbot_role::Select(emote, device.roles[index]);
    Observe(handle);
}
void Observe(void* handle) {
    const auto device = deskbot_role::Describe(static_cast<emote_handle_t>(handle));
    deskbot_wifi_expression::Observe(device);
    Saved saved{};
    uint64_t ticket = 0;
    bool write = false;
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (control) write = control->Observe(device, saved, ticket);
    }
    if (!write) return;
    const bool ok = Save(saved);
    std::lock_guard<std::mutex> lock(mutex);
    if (control) control->SavedResult(ticket, saved, ok);
}
}  // namespace deskbot_role_activation
#endif
