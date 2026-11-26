#include "wifi_expression.h"
#if __has_include("wifi_expression.local.h")
#include "wifi_expression.local.h"
#if __has_include("body_network_defaults.local.h")
#include "body_network_config.h"
#endif
#if __has_include("body_network_defaults.local.h") && __has_include("head_touch_wifi.local.h")
#include "body_network_credentials.h"
#endif
#include "application.h"
#include "board.h"
#include "display/emote_display.h"
#include "studio_role_player.h"
#include "studio_runtime_state.h"
#include <ctime>
#include <mutex>
#include <esp_http_client.h>
#include <esp_log.h>
#include <esp_netif.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace deskbot_wifi_expression {
namespace {
constexpr const char* kTag = "DeskBotExprWifi";
std::mutex mutex;
Control control;
Binding mirror;
uint64_t usb_deadline = 0;
char device_hex[33]{}, boot_hex[33]{}, firmware_hex[65]{}, boot_id[38]{};
// Only the network worker owns these buffers. No TLS or parser allocation is
// made in a display callback, gesture callback or the application scheduler.
char request_body[800];
struct ReplyBuffer { char bytes[kReplyBytes]{}; size_t used = 0; bool overflow = false; } reply;
Command decoded;
uint64_t Now() { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }
bool NetworkReady() {
    auto* station = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip{};
    return station && esp_netif_is_netif_up(station) && esp_netif_get_ip_info(station, &ip) == ESP_OK
        && ip.ip.addr && std::time(nullptr) >= 1700000000;
}
bool OwnerReady() {
    const auto display = deskbot_studio_runtime::ReadDisplay();
    const auto& a = display.runtime.application;
    const auto& p = display.runtime.page;
    return display.player.value == deskbot_studio_display::Player::Initialized
        && a.revision && a.phase == deskbot_studio_runtime::Phase::Settled && a.value == kDeviceStateIdle
        && p.revision && p.phase == deskbot_studio_runtime::Phase::Settled
        && p.value == static_cast<uint8_t>(deskbot_studio_runtime::Page::Home);
}
void Dispatch(uint64_t ticket) {
    // This function runs on the existing application queue. Read the actual
    // current role here; the worker itself uses only the copied mirror.
    auto* display = dynamic_cast<emote::EmoteDisplay*>(Board::GetInstance().GetDisplay());
    const auto handle = display ? display->GetEmoteHandle() : nullptr;
    const auto binding = CopyBinding(deskbot_role::Describe(handle));
    View view{binding, OwnerReady(), false};
    char semantic[8]{};
    {
        std::lock_guard<std::mutex> lock(mutex);
        view.usb_active = Now() < usb_deadline;
        if (!control.Begin(ticket, Now(), view, semantic)) return;
    }
    // Network expressions are visual output only. The same role player owns
    // layout, mirroring and transitions; base actions have their own commands.
    esp_err_t result = ESP_FAIL;
    const bool submitted = handle && deskbot_role::Replay(handle, semantic, result) && result == ESP_OK;
    {
        std::lock_guard<std::mutex> lock(mutex);
        control.Complete(ticket, submitted);
    }
}
esp_err_t OnHttp(esp_http_client_event_t* event) {
    if (event->event_id != HTTP_EVENT_ON_DATA) return ESP_OK;
    auto& buffer = *static_cast<ReplyBuffer*>(event->user_data);
    if (event->data_len < 0 || buffer.used + static_cast<size_t>(event->data_len) >= sizeof(buffer.bytes)) {
        buffer.overflow = true; return ESP_FAIL;
    }
    std::memcpy(buffer.bytes + buffer.used, event->data, static_cast<size_t>(event->data_len));
    buffer.used += static_cast<size_t>(event->data_len);
    return ESP_OK;
}
int Send(int size) {
    reply = {};
    esp_http_client_config_t config{};
#if __has_include("body_network_defaults.local.h")
    config.url = deskbot_body_network::ExpressionUrl();
#else
    config.url = kExpressionUrl;
#endif
    config.cert_pem = kExpressionCa; config.common_name = kExpressionServerName;
    const char* authorization = kExpressionAuthorization;
#if __has_include("body_network_defaults.local.h") && __has_include("head_touch_wifi.local.h")
    const auto credentials = deskbot_body_credentials::Active();
    if (credentials) {
        config.cert_pem = credentials->Certificate(deskbot_body_credentials::Channel::Expression);
        config.cert_len = credentials->cert_size[1];
        authorization = credentials->authorization[1];
    }
#endif
    config.method = HTTP_METHOD_POST; config.timeout_ms = 3500;
    config.buffer_size = 1024; config.buffer_size_tx = 1024;
    config.disable_auto_redirect = true; config.max_authorization_retries = -1;
    config.event_handler = OnHttp; config.user_data = &reply;
    auto client = esp_http_client_init(&config);
    if (!client) return 0;
    int status = 0;
    if (esp_http_client_set_header(client, "Content-Type", "application/json") == ESP_OK
        && esp_http_client_set_header(client, "Authorization", authorization) == ESP_OK
        && esp_http_client_set_post_field(client, request_body, size) == ESP_OK
        && esp_http_client_perform(client) == ESP_OK && !reply.overflow)
        status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    return status;
}
bool PollOnce() {
    if (!NetworkReady()) return false;
    View view{}; Receipt sent{};
    const uint64_t query = Now();
    const bool owner = OwnerReady();
    bool ready = false;
    {
        std::lock_guard<std::mutex> lock(mutex);
        control.Expire(query); sent = control.ReadReceipt();
        view = {mirror, owner, query < usb_deadline};
        ready = owner && mirror.available && !view.usb_active && control.Available();
    }
    const auto size = Encode(request_body, sizeof(request_body), device_hex, boot_hex, firmware_hex,
                             query, view.binding, ready, sent);
    if (!size) return false;
    const int status = Send(size);
    if (status != 200 && status != 204) return false;
    if (status == 200 && !Decode(reply.bytes, reply.used, decoded)) return false;
    uint64_t ticket = 0;
    {
        std::lock_guard<std::mutex> lock(mutex);
        // A result produced during TLS must survive confirmation of an older
        // empty poll. Confirm matches exactly the receipt that was transmitted.
        control.Confirm(sent);
        view.binding = mirror; view.usb_active = Now() < usb_deadline;
        if (status == 200 && !control.Accept(decoded, boot_id, query, Now(), view, ticket)) return false;
    }
    // The bounded command lock never spans the scheduler or role player.
    if (ticket) Application::GetInstance().Schedule([ticket] { Dispatch(ticket); });
    return true;
}
void Run(void*) {
    unsigned attempt = 0;
    constexpr uint32_t delays[] = {1500, 3000, 6000, 12000, 30000};
    for (;;) {
        if (PollOnce()) attempt = 0;
        else if (attempt < 4) ++attempt;
        vTaskDelay(pdMS_TO_TICKS(delays[attempt]));
    }
}
}  // namespace
bool Prepare(const char* device, const char* boot, const char* firmware) {
    if (!Hex(device, 32) || !Hex(boot, 32) || !Hex(firmware, 64)) return false;
    std::strcpy(device_hex, device); std::strcpy(boot_hex, boot); std::strcpy(firmware_hex, firmware);
    std::snprintf(boot_id, sizeof(boot_id), "boot-%s", boot);
    return true;
}
bool Poll() { return PollOnce(); }
void Start(const char* device, const char* boot, const char* firmware) {
    if (!Prepare(device, boot, firmware)) return;
    if (xTaskCreate(Run, "expr_wifi", 6144, nullptr, 1, nullptr) == pdPASS) ESP_LOGI(kTag, "ready");
    else ESP_LOGW(kTag, "task allocation unavailable");
}
void Observe(const deskbot_role::Details& details) {
    const auto binding = CopyBinding(details);
    std::lock_guard<std::mutex> lock(mutex);
    mirror = binding;
}
void UsbLease(bool active, uint64_t deadline) {
    std::lock_guard<std::mutex> lock(mutex);
    usb_deadline = active ? deadline : 0;
}
}  // namespace deskbot_wifi_expression
#endif
