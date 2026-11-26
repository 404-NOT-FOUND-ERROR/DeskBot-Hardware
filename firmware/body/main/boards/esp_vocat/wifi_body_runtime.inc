#include "wifi_body_runtime.h"
#if __has_include("head_touch_wifi.local.h") && __has_include("wifi_expression.local.h")
#if __has_include("body_network_defaults.local.h")
#include "body_network_defaults.local.h"
#include "body_network_config.inc"
#include "body_network_credentials.inc"
#endif
#include "head_touch_wifi.inc"
#include "wifi_expression.inc"

namespace deskbot_body_wifi {
namespace {
std::atomic<bool> started{false};
uint64_t Now() { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }
#if __has_include("body_network_defaults.local.h")
char probe_device[40], probe_firmware[65];

bool TestReceiver(const char* url, const char* name, const char* cert, size_t cert_len, const char* auth) {
    if (std::time(nullptr) < 1700000000) return false;
    esp_http_client_config_t config{};
    config.url = url;
    config.common_name = name;
    config.cert_pem = cert;
    config.cert_len = cert_len;
    config.method = HTTP_METHOD_HEAD;
    config.timeout_ms = 3500;
    config.buffer_size = 1024;
    config.buffer_size_tx = 1024;
    config.disable_auto_redirect = true;
    config.max_authorization_retries = -1;
    auto client = esp_http_client_init(&config);
    if (!client) return false;
    const bool verified = esp_http_client_set_header(client, "Authorization", auth) == ESP_OK
        && esp_http_client_set_header(client, "X-DeskBot-Device", probe_device) == ESP_OK
        && esp_http_client_set_header(client, "X-DeskBot-Firmware", probe_firmware) == ESP_OK
        && esp_http_client_perform(client) == ESP_OK && esp_http_client_get_status_code(client) == 204;
    esp_http_client_cleanup(client);
    return verified;
}
#endif
void Run(void*) {
    Schedule schedule;
#if __has_include("body_network_defaults.local.h")
    auto last_profile = deskbot_body_credentials::Active();
#endif
    for (;;) {
#if __has_include("body_network_defaults.local.h")
        if (deskbot_body_network::ApplyUrls()) schedule.Reconnect();
        const auto profile = deskbot_body_credentials::Active();
        if (profile != last_profile) {
            schedule.Reconnect();
            last_profile = profile;
        }
        const auto trial = deskbot_body_credentials::TakeTrial(Now(), deskbot_body_network::AppliedRevision());
        if (trial.ticket) {
            using KeyChannel = deskbot_body_credentials::Channel;
            const auto& pair = trial.profile;
            const bool touch = TestReceiver(deskbot_body_network::TouchUrl(), kTouchServerName,
                pair ? pair->Certificate(KeyChannel::Touch) : kTouchCa,
                pair ? pair->cert_size[0] : 0, pair ? pair->authorization[0] : kTouchAuthorization);
            const bool expression = TestReceiver(deskbot_body_network::ExpressionUrl(), kExpressionServerName,
                pair ? pair->Certificate(KeyChannel::Expression) : kExpressionCa,
                pair ? pair->cert_size[1] : 0, pair ? pair->authorization[1] : kExpressionAuthorization);
            deskbot_body_credentials::FinishTrial(trial, touch, expression);
        }
#endif
        // Each call finishes/cleans its HTTP client before the other begins.
        // The UI and gesture callbacks never enter this worker or wait on TLS.
        switch (schedule.Next(Now())) {
            case Channel::Touch: {
                const auto delay = deskbot_head_touch_wifi::Poll();
                schedule.CompleteTouch(Now(), delay);
                break;
            }
            case Channel::Expression: {
                const bool success = deskbot_wifi_expression::Poll();
                schedule.CompleteExpression(Now(), success);
                break;
            }
            case Channel::None: break;
        }
        vTaskDelay(pdMS_TO_TICKS(schedule.SleepMs(Now())));
    }
}
}  // namespace
void Start(const char* device, const char* boot, const char* firmware) {
    bool expected = false;
    if (!started.compare_exchange_strong(expected, true)) return;
#if __has_include("body_network_defaults.local.h")
    if (!deskbot_body_network::Initialize(kBodyNetworkHost, kBodyTouchPort, kBodyExpressionPort)) {
        started.store(false); return;
    }
    if (!deskbot_studio_link::Hex(device, 32) || !deskbot_studio_link::Hex(firmware, 64)) {
        started.store(false); return;
    }
    std::snprintf(probe_device, sizeof(probe_device), "device-%s", device);
    std::memcpy(probe_firmware, firmware, sizeof(probe_firmware));
    if (!deskbot_body_credentials::Initialize(kBodyBuiltinCredentials)) {
        started.store(false); return;
    }
#endif
    if (deskbot_head_touch_wifi::Prepare(device, boot, firmware)
        && deskbot_wifi_expression::Prepare(device, boot, firmware)
        && xTaskCreate(Run, "body_wifi", 6144, nullptr, 1, nullptr) == pdPASS) {
        ESP_LOGI("DeskBotBodyWifi", "ready");
        return;
    }
    // Only startup can reach this branch; no task owns the queue yet.
    deskbot_head_touch_wifi::Disable();
    started.store(false);
    ESP_LOGW("DeskBotBodyWifi", "task allocation unavailable");
}
}  // namespace deskbot_body_wifi
#endif
