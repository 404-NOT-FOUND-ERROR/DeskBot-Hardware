#include "wifi_body_runtime.h"
#if __has_include("head_touch_wifi.local.h") && __has_include("wifi_expression.local.h")
#if __has_include("body_network_defaults.local.h")
#include "body_network_defaults.local.h"
#include "body_network_config.inc"
#endif
#include "head_touch_wifi.inc"
#include "wifi_expression.inc"

namespace deskbot_body_wifi {
namespace {
std::atomic<bool> started{false};
uint64_t Now() { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }
void Run(void*) {
    Schedule schedule;
    for (;;) {
#if __has_include("body_network_defaults.local.h")
        if (deskbot_body_network::ApplyUrls()) schedule.Reconnect();
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
